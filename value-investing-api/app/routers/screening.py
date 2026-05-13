"""Screening router — single ticker analysis and batch screening."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import List

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    BatchScreenRequest,
    DailyScreeningSummary,
    RiskLevel,
    ScreeningResult,
    TickerUniverse,
)
from app.services.quality_checker import check_cash_flow_quality
from app.services.screener import calculate_altman_z_score, calculate_piotroski_fscore
from app.services.valuation import calculate_dcf_valuation
from app.utils.validators import validate_ticker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/screen", tags=["Screening"])

TICKERS_PATH = Path(__file__).parent.parent / "data" / "tickers.json"


async def _run_full_analysis(ticker: str) -> ScreeningResult:
    """Run all analyses concurrently for a single ticker."""
    metrics_task = calculate_altman_z_score(ticker)
    fscore_task = calculate_piotroski_fscore(ticker)
    valuation_task = calculate_dcf_valuation(ticker)
    quality_task = check_cash_flow_quality(ticker)

    metrics, (f_score, f_breakdown), valuation, quality = await asyncio.gather(
        metrics_task, fscore_task, valuation_task, quality_task
    )

    metrics.f_score = f_score
    metrics.f_score_breakdown = f_breakdown

    from app.utils.financial_data import get_info
    info = await get_info(ticker)
    company_name = info.get("longName") or info.get("shortName")

    return ScreeningResult(
        ticker=ticker,
        company_name=company_name,
        financials=metrics,
        valuation=valuation,
        cash_flow_quality=quality,
    )


@router.post("/{ticker}", response_model=ScreeningResult, summary="Analyse a single ticker")
async def screen_single_ticker(ticker: str) -> ScreeningResult:
    """Full value investing analysis for one stock ticker."""
    try:
        clean = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        return await _run_full_analysis(clean)
    except Exception as exc:
        logger.error("Screening failed for %s: %s", clean, exc)
        return ScreeningResult(ticker=clean, error=str(exc))


@router.post("/batch", response_model=DailyScreeningSummary, summary="Batch screen a list of tickers")
async def batch_screen(request: BatchScreenRequest) -> DailyScreeningSummary:
    """
    Screen multiple tickers concurrently.
    Returns a daily summary with top opportunities.
    """
    validated = []
    errors = []
    for t in request.tickers:
        try:
            validated.append(validate_ticker(t))
        except ValueError as exc:
            errors.append(str(exc))

    # Process with bounded concurrency (avoid hammering yfinance)
    semaphore = asyncio.Semaphore(5)

    async def safe_screen(t: str) -> ScreeningResult:
        async with semaphore:
            try:
                return await _run_full_analysis(t)
            except Exception as exc:
                logger.warning("Batch error for %s: %s", t, exc)
                return ScreeningResult(ticker=t, error=str(exc))

    results = await asyncio.gather(*[safe_screen(t) for t in validated])

    summary = DailyScreeningSummary(
        total_screened=len(results),
        errors=errors,
    )
    for r in results:
        if r.error:
            summary.errors.append(f"{r.ticker}: {r.error}")
            continue
        if r.passes_filters:
            summary.passed_filters += 1
        if r.risk_level == RiskLevel.safe:
            summary.safe_zone += 1
        elif r.risk_level == RiskLevel.distress:
            summary.distress_zone += 1
        elif r.risk_level == RiskLevel.grey_zone:
            summary.grey_zone += 1

    top = [
        r for r in results
        if not r.error
        and (r.financials.z_score or 0) >= request.min_z_score
        and (r.financials.f_score or 0) >= request.min_f_score
    ]
    top.sort(key=lambda r: (r.passes_filters, r.financials.z_score or 0), reverse=True)
    summary.top_opportunities = top[:10]

    return summary


@router.get("/opportunities/top", response_model=List[ScreeningResult], summary="Get best opportunities from full universe")
async def get_top_opportunities(
    min_z_score: float = Query(2.0, ge=0, description="Minimum Altman Z-Score"),
    min_f_score: int = Query(6, ge=0, le=9, description="Minimum Piotroski F-Score"),
    limit: int = Query(20, ge=1, le=100),
) -> List[ScreeningResult]:
    """
    Screens the full ticker universe and returns top value opportunities.
    NOTE: this is a slow endpoint (~minutes for S&P500). Use batch-screen for subsets.
    """
    universe = _load_universe()
    tickers = universe.all_tickers[:limit * 5]  # over-fetch then filter

    request = BatchScreenRequest(
        tickers=tickers, min_z_score=min_z_score, min_f_score=min_f_score
    )
    summary = await batch_screen(request)
    return summary.top_opportunities[:limit]


@router.post("/update-universe", summary="Refresh ticker universe from Wikipedia + BMV")
async def update_ticker_universe() -> dict:
    """Download S&P 500 tickers from Wikipedia and augment with BMV top stocks."""
    try:
        sp500 = await _fetch_sp500_tickers()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch S&P 500: {exc}")

    bmv = _default_bmv_tickers()
    universe = {"sp500": sp500, "bmv": bmv}
    TICKERS_PATH.write_text(json.dumps(universe, indent=2))
    return {"sp500_count": len(sp500), "bmv_count": len(bmv), "status": "updated"}


def _load_universe() -> TickerUniverse:
    if TICKERS_PATH.exists():
        data = json.loads(TICKERS_PATH.read_text())
        return TickerUniverse(**data)
    return TickerUniverse(sp500=_default_sp500_sample(), bmv=_default_bmv_tickers())


async def _fetch_sp500_tickers() -> List[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    # Parse the first wikitable
    import re
    pattern = r'<td><a href="/wiki/[^"]*" title="[^"]*">([A-Z]+(?:\.[A-Z])?)</a></td>'
    tickers = re.findall(pattern, resp.text)
    return list(dict.fromkeys(tickers))  # deduplicate preserving order


def _default_sp500_sample() -> List[str]:
    return [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "TSLA",
        "AVGO", "JPM", "UNH", "V", "XOM", "MA", "HD", "PG", "COST", "JNJ", "MRK",
        "ABBV", "CVX", "KO", "BAC", "PEP", "ORCL", "ACN", "TMO", "MCD", "NFLX",
    ]


def _default_bmv_tickers() -> List[str]:
    return [
        "WALMEX.MX", "FEMSA.MX", "GFNORTEO.MX", "AMXL.MX", "CEMEXCPO.MX",
        "GMEXICOB.MX", "BIMBOA.MX", "GRUMAB.MX", "ALSEA.MX", "AC.MX",
        "KIMBERA.MX", "GCARSOA1.MX", "ASURB.MX", "OMAB.MX", "VOLAR.MX",
    ]

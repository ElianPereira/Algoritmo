"""Screening router — single ticker analysis and batch screening."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import save_many, save_screening_result
from app.db.session import async_session_factory, get_session
from app.models.schemas import (
    BatchScreenRequest,
    DailyScreeningSummary,
    RiskLevel,
    ScreeningResult,
    TickerUniverse,
)
from app.services.alerts import maybe_send_alert
from app.services.quality_checker import check_cash_flow_quality
from app.services.screener import calculate_altman_z_score, calculate_piotroski_fscore
from app.services.valuation import calculate_dcf_valuation
from app.utils.validators import validate_ticker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/screen", tags=["Screening"])

TICKERS_PATH = Path(__file__).parent.parent / "data" / "tickers.json"


async def _run_full_analysis(ticker: str) -> ScreeningResult:
    """Run all analyses concurrently for a single ticker."""
    metrics, (f_score, f_breakdown), valuation, quality = await asyncio.gather(
        calculate_altman_z_score(ticker),
        calculate_piotroski_fscore(ticker),
        calculate_dcf_valuation(ticker),
        check_cash_flow_quality(ticker),
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


async def run_batch_logic(
    request: BatchScreenRequest,
    session: AsyncSession,
) -> DailyScreeningSummary:
    """
    Core batch screening logic — callable from both the FastAPI router
    (with DI session) and the scheduler (with a manually created session).
    """
    validated, errors = [], []
    for t in request.tickers:
        try:
            validated.append(validate_ticker(t))
        except ValueError as exc:
            errors.append(str(exc))

    semaphore = asyncio.Semaphore(5)

    async def safe_screen(t: str) -> ScreeningResult:
        async with semaphore:
            try:
                return await _run_full_analysis(t)
            except Exception as exc:
                logger.warning("Batch error for %s: %s", t, exc)
                return ScreeningResult(ticker=t, error=str(exc))

    results = await asyncio.gather(*[safe_screen(t) for t in validated])

    summary = DailyScreeningSummary(total_screened=len(results), errors=errors)
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

    valid_results = [r for r in results if not r.error]
    alert_tasks = [maybe_send_alert(r) for r in valid_results]
    await asyncio.gather(save_many(session, valid_results), *alert_tasks)

    return summary


@router.post("/{ticker}", response_model=ScreeningResult, summary="Analyse a single ticker")
async def screen_single_ticker(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> ScreeningResult:
    """Full value investing analysis for one stock ticker."""
    try:
        clean = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        result = await _run_full_analysis(clean)
        await asyncio.gather(
            save_screening_result(session, result),
            maybe_send_alert(result),
        )
        return result
    except Exception as exc:
        logger.error("Screening failed for %s: %s", clean, exc)
        return ScreeningResult(ticker=clean, error=str(exc))


@router.post("/batch", response_model=DailyScreeningSummary, summary="Batch screen a list of tickers")
async def batch_screen(
    request: BatchScreenRequest,
    session: AsyncSession = Depends(get_session),
) -> DailyScreeningSummary:
    """Screen multiple tickers concurrently."""
    return await run_batch_logic(request, session)


@router.get("/opportunities/top", response_model=List[ScreeningResult], summary="Top opportunities from full universe")
async def get_top_opportunities(
    min_z_score: float = Query(2.0, ge=0),
    min_f_score: int = Query(6, ge=0, le=9),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> List[ScreeningResult]:
    """Screens the full ticker universe and returns top value opportunities."""
    universe = _load_universe()
    tickers = universe.all_tickers[:limit * 5]
    request = BatchScreenRequest(tickers=tickers, min_z_score=min_z_score, min_f_score=min_f_score)
    summary = await run_batch_logic(request, session)
    return summary.top_opportunities[:limit]


@router.post("/update-universe", summary="Refresh ticker universe from Wikipedia (S&P 500 + NYSE)")
async def update_ticker_universe() -> dict:
    """Download S&P 500 tickers from Wikipedia and augment with broad NYSE coverage."""
    try:
        sp500 = await _fetch_sp500_tickers()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch S&P 500: {exc}")

    nyse = _default_nyse_tickers()
    universe = {"sp500": sp500, "nyse": nyse}
    TICKERS_PATH.write_text(json.dumps(universe, indent=2))
    return {"sp500_count": len(sp500), "nyse_count": len(nyse), "status": "updated"}


def _load_universe() -> TickerUniverse:
    if TICKERS_PATH.exists():
        data = json.loads(TICKERS_PATH.read_text())
        if "bmv" in data and "nyse" not in data:
            data["nyse"] = _default_nyse_tickers()
            del data["bmv"]
            TICKERS_PATH.write_text(json.dumps(data, indent=2))
        return TickerUniverse(**data)
    return TickerUniverse(sp500=_default_sp500_sample(), nyse=_default_nyse_tickers())


async def _fetch_sp500_tickers() -> List[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    import re
    pattern = r'<td><a href="/wiki/[^"]*" title="[^"]*">([A-Z]+(?:\.[A-Z])?)</a></td>'
    tickers = re.findall(pattern, resp.text)
    return list(dict.fromkeys(tickers))


def _default_sp500_sample() -> List[str]:
    return [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "TSLA",
        "AVGO", "JPM", "UNH", "V", "XOM", "MA", "HD", "PG", "COST", "JNJ", "MRK",
        "ABBV", "CVX", "KO", "BAC", "PEP", "ORCL", "ACN", "TMO", "MCD", "NFLX",
    ]


def _default_nyse_tickers() -> List[str]:
    """Broad NYSE-listed universe accessible via GBM+ direct US market."""
    return [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "AXP", "USB", "TFC",
        "PNC", "SCHW", "MCO", "ICE", "CME", "CB", "MMC", "AON", "TRV", "ALL",
        "JNJ", "PFE", "MRK", "ABT", "BMY", "MDT", "UNH", "ELV", "HUM", "CI",
        "SYK", "BDX", "ZBH", "BAX", "CAH", "MCK", "ABC", "DHR", "TMO", "IQV",
        "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "VLO", "MPC", "OXY", "HAL",
        "BKR", "DVN", "HES", "MRO", "APA", "FANG", "PXD", "KMI", "WMB", "OKE",
        "PG", "KO", "PEP", "WMT", "COST", "CL", "KMB", "GIS", "K", "HRL",
        "SJM", "MKC", "CAG", "CPB", "TSN", "KHC", "MO", "PM", "BTI", "STZ",
        "MCD", "NKE", "HD", "LOW", "TGT", "TJX", "ROST", "DG", "DLTR", "BBY",
        "F", "GM", "WHR", "RL", "PVH", "HBI", "VFC", "LKQ", "AN", "KMX",
        "GE", "CAT", "DE", "HON", "MMM", "UPS", "FDX", "LMT", "RTX", "NOC",
        "GD", "BA", "EMR", "ETN", "PH", "ROK", "AME", "XYL", "IR", "ITW",
        "LIN", "APD", "ECL", "SHW", "PPG", "NEM", "FCX", "NUE", "STLD", "CLF",
        "AA", "CF", "MOS", "FMC", "ALB", "CE", "EMN", "RPM", "IFF", "DD",
        "AMT", "PLD", "CCI", "EQIX", "SPG", "O", "AVB", "EQR", "PSA", "WY",
        "VTR", "WELL", "ARE", "BXP", "KIM", "REG", "EXR", "CUBE", "LSI", "MAA",
        "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PCG", "ETR", "FE",
        "XEL", "ES", "WEC", "CMS", "DTE", "PPL", "AEE", "CNP", "NI", "PNW",
        "IBM", "HPE", "HPQ", "DELL", "CDW", "JNPR", "GLW", "TEL", "HPQ", "XRX",
    ]

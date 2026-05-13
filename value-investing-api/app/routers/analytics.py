"""Analytics router — historical results, daily summaries, and manual batch trigger."""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import (
    get_daily_summaries,
    get_history_for_ticker,
    get_latest_for_ticker,
    get_opportunities,
)
from app.db.session import get_session
from app.models.database import DailySummaryORM, ScreeningResultORM

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["Analytics"])


def _orm_to_dict(row: ScreeningResultORM) -> dict:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "company_name": row.company_name,
        "screening_date": row.screening_date.isoformat(),
        "z_score": row.z_score,
        "f_score": row.f_score,
        "pe_ratio": row.pe_ratio,
        "debt_to_equity": row.debt_to_equity,
        "current_ratio": row.current_ratio,
        "current_price": row.current_price,
        "fair_value_dcf": row.fair_value_dcf,
        "safety_margin": row.safety_margin,
        "upside_potential": row.upside_potential,
        "quality_ratio": row.quality_ratio,
        "is_suspicious": row.is_suspicious,
        "risk_level": row.risk_level,
        "passes_filters": row.passes_filters,
        "error": row.error,
    }


def _summary_to_dict(row: DailySummaryORM) -> dict:
    return {
        "id": row.id,
        "screening_date": row.screening_date.isoformat(),
        "total_screened": row.total_screened,
        "passed_filters": row.passed_filters,
        "safe_zone": row.safe_zone,
        "grey_zone": row.grey_zone,
        "distress_zone": row.distress_zone,
    }


@router.get(
    "/history/{ticker}",
    summary="Historical screening results for a ticker",
)
async def get_ticker_history(
    ticker: str,
    limit: int = Query(30, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Returns the most recent `limit` screening records for the given ticker."""
    rows = await get_history_for_ticker(session, ticker.upper(), limit=limit)
    return [_orm_to_dict(r) for r in rows]


@router.get(
    "/latest/{ticker}",
    summary="Most recent screening result for a ticker",
)
async def get_latest(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await get_latest_for_ticker(session, ticker.upper())
    if not row:
        raise HTTPException(status_code=404, detail=f"No screening data found for {ticker}")
    return _orm_to_dict(row)


@router.get(
    "/opportunities",
    summary="Persisted opportunities filtered from DB (no live analysis)",
)
async def get_stored_opportunities(
    min_z_score: float = Query(1.8, ge=0),
    min_f_score: int = Query(5, ge=0, le=9),
    limit: int = Query(20, ge=1, le=100),
    for_date: Optional[date] = Query(None, description="Filter by screening date (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:
    """
    Returns opportunities that were previously screened and saved to the database.
    Much faster than live screening — use for dashboards and reporting.
    """
    rows = await get_opportunities(
        session, min_z_score=min_z_score, min_f_score=min_f_score,
        limit=limit, for_date=for_date,
    )
    return [_orm_to_dict(r) for r in rows]


@router.get("/summaries", summary="Daily batch screening summaries")
async def list_daily_summaries(
    limit: int = Query(30, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Returns the last `limit` daily batch summaries."""
    rows = await get_daily_summaries(session, limit=limit)
    return [_summary_to_dict(r) for r in rows]


@router.post("/trigger-batch", summary="Manually trigger the daily batch screening job")
async def trigger_batch(background_tasks: BackgroundTasks) -> dict:
    """
    Kick off a full universe batch screening run in the background.
    Results will be persisted and Telegram alerts fired as usual.
    The endpoint returns immediately — poll /analytics/summaries for results.
    """
    from app.services.scheduler import run_daily_batch

    background_tasks.add_task(run_daily_batch)
    return {"status": "batch_started", "message": "Full universe screening triggered. Check /analytics/summaries for results."}


@router.get("/scheduler/status", summary="Current APScheduler status")
async def scheduler_status() -> dict:
    from app.services.scheduler import get_scheduler
    sched = get_scheduler()
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"running": sched.running, "jobs": jobs}

"""Data access layer — save and query screening results."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import DailySummaryORM, ScreeningResultORM
from app.models.schemas import DailyScreeningSummary, ScreeningResult

logger = logging.getLogger(__name__)


def _to_orm(result: ScreeningResult) -> ScreeningResultORM:
    fin = result.financials
    val = result.valuation
    cfq = result.cash_flow_quality
    return ScreeningResultORM(
        ticker=result.ticker,
        company_name=result.company_name,
        screening_date=result.screening_date,
        z_score=fin.z_score,
        f_score=fin.f_score,
        pe_ratio=fin.pe_ratio,
        pb_ratio=fin.pb_ratio,
        debt_to_equity=fin.debt_to_equity,
        current_ratio=fin.current_ratio,
        current_price=val.current_price,
        fair_value_dcf=val.fair_value_dcf,
        safety_margin=val.safety_margin,
        upside_potential=val.upside_potential,
        quality_ratio=cfq.quality_ratio,
        is_suspicious=cfq.is_suspicious,
        risk_level=result.risk_level.value,
        passes_filters=result.passes_filters,
        financials_json=fin.model_dump_json(),
        valuation_json=val.model_dump_json(),
        downside_risks_json=json.dumps(result.downside_risks),
        error=result.error,
    )


async def save_screening_result(session: AsyncSession, result: ScreeningResult) -> ScreeningResultORM:
    orm = _to_orm(result)
    session.add(orm)
    await session.commit()
    await session.refresh(orm)
    return orm


async def save_many(session: AsyncSession, results: List[ScreeningResult]) -> int:
    saved = 0
    for r in results:
        try:
            session.add(_to_orm(r))
            saved += 1
        except Exception as exc:
            logger.warning("Failed to serialize %s for DB: %s", r.ticker, exc)
    await session.commit()
    return saved


async def save_daily_summary(session: AsyncSession, summary: DailyScreeningSummary) -> DailySummaryORM:
    orm = DailySummaryORM(
        screening_date=summary.screening_date,
        total_screened=summary.total_screened,
        passed_filters=summary.passed_filters,
        safe_zone=summary.safe_zone,
        grey_zone=summary.grey_zone,
        distress_zone=summary.distress_zone,
        top_opportunities_json=json.dumps([r.model_dump(mode="json") for r in summary.top_opportunities]),
        errors_json=json.dumps({"errors": summary.errors, "no_data": summary.no_data_count}),
    )
    session.add(orm)
    await session.commit()
    await session.refresh(orm)
    return orm


async def get_latest_for_ticker(
    session: AsyncSession, ticker: str
) -> Optional[ScreeningResultORM]:
    stmt = (
        select(ScreeningResultORM)
        .where(ScreeningResultORM.ticker == ticker)
        .order_by(ScreeningResultORM.screening_date.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_history_for_ticker(
    session: AsyncSession, ticker: str, limit: int = 30
) -> List[ScreeningResultORM]:
    stmt = (
        select(ScreeningResultORM)
        .where(ScreeningResultORM.ticker == ticker)
        .order_by(ScreeningResultORM.screening_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_opportunities(
    session: AsyncSession,
    min_z_score: float = 1.8,
    min_f_score: int = 5,
    limit: int = 20,
    for_date: Optional[date] = None,
) -> List[ScreeningResultORM]:
    filters = [
        ScreeningResultORM.passes_filters == True,
        ScreeningResultORM.z_score >= min_z_score,
        ScreeningResultORM.f_score >= min_f_score,
    ]
    if for_date:
        day_start = datetime.combine(for_date, datetime.min.time())
        day_end = datetime.combine(for_date, datetime.max.time())
        filters.append(
            and_(
                ScreeningResultORM.screening_date >= day_start,
                ScreeningResultORM.screening_date <= day_end,
            )
        )
    stmt = (
        select(ScreeningResultORM)
        .where(and_(*filters))
        .order_by(ScreeningResultORM.z_score.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_daily_summaries(
    session: AsyncSession, limit: int = 30
) -> List[DailySummaryORM]:
    stmt = (
        select(DailySummaryORM)
        .order_by(DailySummaryORM.screening_date.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())

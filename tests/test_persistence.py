"""
Integration tests for the DB persistence layer.
Uses an in-memory SQLite database — no real DB required.
"""
from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.repository import (
    get_history_for_ticker,
    get_latest_for_ticker,
    get_opportunities,
    save_daily_summary,
    save_many,
    save_screening_result,
)
from app.models.database import Base
from app.models.schemas import (
    CashFlowQuality,
    DailyScreeningSummary,
    FinancialMetrics,
    ScreeningResult,
    ValuationData,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s

    await engine.dispose()


def _make_result(ticker="AAPL", z_score=3.5, f_score=8, cf_ratio=1.1, passes=True) -> ScreeningResult:
    return ScreeningResult(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        screening_date=datetime.utcnow(),
        financials=FinancialMetrics(z_score=z_score, f_score=f_score),
        valuation=ValuationData(current_price=150.0, fair_value_dcf=200.0, safety_margin=25.0),
        cash_flow_quality=CashFlowQuality(
            net_income=100_000_000,
            operating_cash_flow=int(100_000_000 * cf_ratio),
        ),
    )


class TestSaveAndRetrieve:
    @pytest.mark.asyncio
    async def test_save_and_get_latest(self, session):
        result = _make_result("MSFT", z_score=4.0)
        await save_screening_result(session, result)

        row = await get_latest_for_ticker(session, "MSFT")
        assert row is not None
        assert row.ticker == "MSFT"
        assert row.z_score == 4.0

    @pytest.mark.asyncio
    async def test_get_latest_returns_none_for_unknown(self, session):
        row = await get_latest_for_ticker(session, "UNKNOWN")
        assert row is None

    @pytest.mark.asyncio
    async def test_history_returns_multiple_records(self, session):
        for _ in range(3):
            await save_screening_result(session, _make_result("GOOG"))

        rows = await get_history_for_ticker(session, "GOOG", limit=10)
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_history_limit_respected(self, session):
        for _ in range(5):
            await save_screening_result(session, _make_result("AMZN"))

        rows = await get_history_for_ticker(session, "AMZN", limit=2)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_save_many(self, session):
        results = [_make_result(f"T{i}") for i in range(5)]
        count = await save_many(session, results)
        assert count == 5

    @pytest.mark.asyncio
    async def test_passes_filters_stored_correctly(self, session):
        good = _make_result("GOOD", z_score=3.5, f_score=8, cf_ratio=1.2)
        bad = _make_result("BAD", z_score=1.0, f_score=3, cf_ratio=0.5)
        await save_many(session, [good, bad])

        good_row = await get_latest_for_ticker(session, "GOOD")
        bad_row = await get_latest_for_ticker(session, "BAD")

        assert good_row.passes_filters is True
        assert bad_row.passes_filters is False


class TestGetOpportunities:
    @pytest.mark.asyncio
    async def test_filters_by_z_score(self, session):
        await save_many(session, [
            _make_result("HIGH_Z", z_score=4.0, f_score=8),
            _make_result("LOW_Z", z_score=1.5, f_score=8),
        ])
        rows = await get_opportunities(session, min_z_score=2.0, min_f_score=5)
        tickers = {r.ticker for r in rows}
        assert "HIGH_Z" in tickers
        assert "LOW_Z" not in tickers

    @pytest.mark.asyncio
    async def test_filters_by_f_score(self, session):
        await save_many(session, [
            _make_result("HIGH_F", z_score=3.5, f_score=8),
            _make_result("LOW_F", z_score=3.5, f_score=3),
        ])
        rows = await get_opportunities(session, min_z_score=1.8, min_f_score=5)
        tickers = {r.ticker for r in rows}
        assert "HIGH_F" in tickers
        # LOW_F has f_score=3 AND passes_filters=False (f_score < 5)
        assert "LOW_F" not in tickers

    @pytest.mark.asyncio
    async def test_limit_respected(self, session):
        results = [_make_result(f"T{i}", z_score=3.5, f_score=8) for i in range(10)]
        await save_many(session, results)
        rows = await get_opportunities(session, min_z_score=1.8, min_f_score=5, limit=3)
        assert len(rows) <= 3


class TestDailySummary:
    @pytest.mark.asyncio
    async def test_save_and_list_summary(self, session):
        summary = DailyScreeningSummary(
            total_screened=50,
            passed_filters=5,
            safe_zone=4,
            grey_zone=1,
            distress_zone=45,
        )
        orm = await save_daily_summary(session, summary)
        assert orm.id is not None
        assert orm.total_screened == 50
        assert orm.passed_filters == 5

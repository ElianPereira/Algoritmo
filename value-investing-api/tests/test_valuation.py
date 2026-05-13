"""
Unit tests for valuation.py — DCF multi-scenario.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from app.services.valuation import (
    _compute_wacc,
    _dcf_value,
    _project_fcf,
    calculate_dcf_valuation,
)


# ── Pure-function tests (no I/O) ──────────────────────────────────────────────

class TestComputeWACC:
    def test_standard_inputs(self):
        wacc = _compute_wacc(beta=1.0, cost_of_debt=0.05, tax_rate=0.21, d_ratio=0.3)
        assert 0.06 <= wacc <= 0.15

    def test_high_beta_increases_wacc(self):
        low = _compute_wacc(beta=0.5, cost_of_debt=0.05, tax_rate=0.21, d_ratio=0.3)
        high = _compute_wacc(beta=2.0, cost_of_debt=0.05, tax_rate=0.21, d_ratio=0.3)
        assert high > low

    def test_floor_at_six_percent(self):
        """Very low beta / debt shouldn't produce WACC below 6%."""
        wacc = _compute_wacc(beta=0.1, cost_of_debt=0.01, tax_rate=0.21, d_ratio=0.0)
        assert wacc >= 0.06


class TestProjectFCF:
    def test_growth_applied(self):
        fcfs = _project_fcf(base_fcf=100.0, growth_rate=0.10, years=3)
        assert len(fcfs) == 3
        assert abs(fcfs[0] - 110.0) < 0.01
        assert abs(fcfs[1] - 121.0) < 0.01
        assert abs(fcfs[2] - 133.1) < 0.01

    def test_zero_growth(self):
        fcfs = _project_fcf(base_fcf=100.0, growth_rate=0.0, years=5)
        assert all(abs(f - 100.0) < 0.01 for f in fcfs)

    def test_negative_growth(self):
        fcfs = _project_fcf(base_fcf=100.0, growth_rate=-0.05, years=2)
        assert fcfs[0] < 100.0
        assert fcfs[1] < fcfs[0]


class TestDCFValue:
    def test_discounts_properly(self):
        fcfs = [100.0] * 10
        terminal_value = 1000.0
        wacc = 0.10
        pv = _dcf_value(fcfs, terminal_value, wacc)
        assert pv > 0
        # Manual check: sum of PV of $100/yr for 10 yrs + terminal
        import math
        manual = sum(100 / (1.1 ** t) for t in range(1, 11)) + 1000 / (1.1 ** 10)
        assert abs(pv - manual) < 0.01

    def test_higher_wacc_lower_value(self):
        fcfs = [100.0] * 10
        pv_low = _dcf_value(fcfs, 1000.0, wacc=0.05)
        pv_high = _dcf_value(fcfs, 1000.0, wacc=0.15)
        assert pv_low > pv_high


# ── Integration test with mocked yfinance ────────────────────────────────────

def _make_cashflow() -> pd.DataFrame:
    data = {
        "2023-12-31": {"Operating Cash Flow": 40_000_000, "Capital Expenditure": -5_000_000},
        "2022-12-31": {"Operating Cash Flow": 35_000_000, "Capital Expenditure": -4_000_000},
        "2021-12-31": {"Operating Cash Flow": 30_000_000, "Capital Expenditure": -3_500_000},
    }
    return pd.DataFrame(data)


def _make_balance_sheet() -> pd.DataFrame:
    data = {
        "2023-12-31": {"Total Debt": 60_000_000},
        "2022-12-31": {"Total Debt": 70_000_000},
    }
    return pd.DataFrame(data)


def _make_income_stmt() -> pd.DataFrame:
    data = {
        "2023-12-31": {"Interest Expense": -3_000_000},
        "2022-12-31": {"Interest Expense": -3_500_000},
    }
    return pd.DataFrame(data)


MOCK_INFO = {
    "currentPrice": 20.0,
    "sharesOutstanding": 10_000_000,
    "beta": 1.2,
    "marketCap": 200_000_000,
    "revenueGrowth": 0.10,
}

MOCK_FINS = {
    "cashflow": _make_cashflow(),
    "balance_sheet": _make_balance_sheet(),
    "income_stmt": _make_income_stmt(),
}


@pytest.mark.asyncio
async def test_dcf_returns_valuation_data():
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        result = await calculate_dcf_valuation("TEST")

    assert result.current_price == 20.0
    assert result.fair_value_dcf is not None
    assert result.fair_value_dcf > 0
    assert result.scenarios is not None
    assert len(result.scenarios) == 3


@pytest.mark.asyncio
async def test_dcf_scenario_names():
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        result = await calculate_dcf_valuation("TEST")

    names = {s.name for s in result.scenarios}
    assert names == {"pessimistic", "base", "optimistic"}


@pytest.mark.asyncio
async def test_dcf_scenario_weights_sum_to_one():
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        result = await calculate_dcf_valuation("TEST")

    total_weight = sum(s.weight for s in result.scenarios)
    assert abs(total_weight - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_dcf_pessimistic_lt_optimistic():
    """Pessimistic scenario fair value must be lower than optimistic."""
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        result = await calculate_dcf_valuation("TEST")

    scenarios = {s.name: s for s in result.scenarios}
    assert scenarios["pessimistic"].fair_value < scenarios["optimistic"].fair_value


@pytest.mark.asyncio
async def test_dcf_upside_computed():
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        result = await calculate_dcf_valuation("TEST")

    # upside_potential is auto-computed by model_validator
    if result.fair_value_dcf and result.current_price:
        expected = round(
            (result.fair_value_dcf - result.current_price) / result.current_price * 100, 2
        )
        assert abs((result.upside_potential or 0) - expected) < 0.01


@pytest.mark.asyncio
async def test_dcf_negative_fcf_returns_partial():
    """Negative FCF should return a ValuationData with current_price but no fair_value."""
    negative_cf_fins = {
        "cashflow": pd.DataFrame({
            "2023-12-31": {"Operating Cash Flow": -1_000_000, "Capital Expenditure": -500_000}
        }),
        "balance_sheet": _make_balance_sheet(),
        "income_stmt": _make_income_stmt(),
    }
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value=negative_cf_fins)),
    ):
        result = await calculate_dcf_valuation("NEGATIVE")

    assert result.current_price == 20.0
    assert result.fair_value_dcf is None


@pytest.mark.asyncio
async def test_dcf_missing_data_no_crash():
    """Completely missing data should return empty ValuationData without raising."""
    with (
        patch("app.services.valuation.get_info", new=AsyncMock(return_value={})),
        patch("app.services.valuation.get_financials", new=AsyncMock(return_value={})),
    ):
        result = await calculate_dcf_valuation("EMPTY")

    assert result.fair_value_dcf is None

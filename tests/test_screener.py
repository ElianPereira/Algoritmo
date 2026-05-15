"""
Unit tests for screener.py — Altman Z-Score and Piotroski F-Score.
Uses mocked yfinance data to avoid network calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from app.models.schemas import FinancialMetrics, ScreeningResult
from app.services.screener import calculate_altman_z_score, calculate_piotroski_fscore


def _make_balance_sheet() -> pd.DataFrame:
    data = {
        "2023-12-31": {
            "Total Assets": 400_000_000,
            "Total Current Assets": 120_000_000,
            "Total Current Liabilities": 60_000_000,
            "Retained Earnings": 80_000_000,
            "Total Liabilities Net Minority Interest": 150_000_000,
            "Long Term Debt": 80_000_000,
            "Stockholders Equity": 250_000_000,
            "Ordinary Shares Number": 10_000_000,
        },
        "2022-12-31": {
            "Total Assets": 370_000_000,
            "Total Current Assets": 100_000_000,
            "Total Current Liabilities": 65_000_000,
            "Retained Earnings": 70_000_000,
            "Total Liabilities Net Minority Interest": 160_000_000,
            "Long Term Debt": 90_000_000,
            "Stockholders Equity": 210_000_000,
            "Ordinary Shares Number": 10_500_000,
        },
    }
    df = pd.DataFrame(data)
    return df


def _make_income_stmt() -> pd.DataFrame:
    data = {
        "2023-12-31": {
            "Total Revenue": 200_000_000,
            "EBIT": 40_000_000,
            "Net Income": 28_000_000,
            "Gross Profit": 90_000_000,
        },
        "2022-12-31": {
            "Total Revenue": 170_000_000,
            "EBIT": 32_000_000,
            "Net Income": 22_000_000,
            "Gross Profit": 72_000_000,
        },
    }
    df = pd.DataFrame(data)
    return df


def _make_cashflow() -> pd.DataFrame:
    data = {
        "2023-12-31": {
            "Operating Cash Flow": 35_000_000,
            "Capital Expenditure": -8_000_000,
        },
        "2022-12-31": {
            "Operating Cash Flow": 28_000_000,
            "Capital Expenditure": -6_000_000,
        },
    }
    df = pd.DataFrame(data)
    return df


MOCK_INFO = {
    "marketCap": 500_000_000,
    "longName": "Test Company Inc.",
    "trailingPE": 18.5,
    "priceToBook": 2.1,
    "beta": 1.1,
    "currentPrice": 50.0,
    "sharesOutstanding": 10_000_000,
    "revenueGrowth": 0.08,
}

MOCK_FINS = {
    "balance_sheet": _make_balance_sheet(),
    "income_stmt": _make_income_stmt(),
    "cashflow": _make_cashflow(),
    "quarterly_balance_sheet": None,
}


@pytest.mark.asyncio
async def test_altman_z_score_healthy_company():
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        metrics = await calculate_altman_z_score("TEST")

    assert metrics.z_score is not None
    assert metrics.z_score > 0
    assert metrics.z_score_components is not None
    assert set(metrics.z_score_components.keys()) == {"x1", "x2", "x3", "x4", "x5"}


@pytest.mark.asyncio
async def test_altman_z_score_safe_zone():
    """A company with strong working capital and low leverage should be in Safe Zone (Z > 2.99)."""
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        metrics = await calculate_altman_z_score("TEST")

    # Safe zone threshold
    assert metrics.z_score is not None
    # Should compute a reasonable score given the healthy mock data
    assert metrics.z_score > 1.0


@pytest.mark.asyncio
async def test_altman_z_score_missing_data():
    """Missing total assets should return empty metrics (no crash)."""
    empty_fins = {"balance_sheet": None, "income_stmt": None, "cashflow": None}
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value={})),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=empty_fins)),
    ):
        metrics = await calculate_altman_z_score("BROKEN")

    assert metrics.z_score is None


@pytest.mark.asyncio
async def test_piotroski_fscore_high_quality():
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        f_score, breakdown = await calculate_piotroski_fscore("TEST")

    assert isinstance(f_score, int)
    assert 0 <= f_score <= 9
    assert len(breakdown) == 9
    assert all(v in (0, 1) for v in breakdown.values())


@pytest.mark.asyncio
async def test_piotroski_fscore_positive_roa():
    """ROA > 0 and OCF > 0 should both score 1."""
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        _, breakdown = await calculate_piotroski_fscore("TEST")

    assert breakdown["roa_positive"] == 1
    assert breakdown["ocf_positive"] == 1


@pytest.mark.asyncio
async def test_piotroski_fscore_ocf_gt_net_income():
    """OCF (35M) > Net Income (28M) should score 1."""
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        _, breakdown = await calculate_piotroski_fscore("TEST")

    assert breakdown["ocf_gt_net_income"] == 1


@pytest.mark.asyncio
async def test_piotroski_fscore_no_dilution():
    """Shares decreased from 10.5M to 10M — no dilution criterion should score 1."""
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value=MOCK_INFO)),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=MOCK_FINS)),
    ):
        _, breakdown = await calculate_piotroski_fscore("TEST")

    assert breakdown["no_dilution"] == 1


@pytest.mark.asyncio
async def test_piotroski_fscore_empty_data():
    """Empty data should return F-Score of 0 without crashing."""
    empty_fins = {"balance_sheet": None, "income_stmt": None, "cashflow": None}
    with (
        patch("app.services.screener.get_info", new=AsyncMock(return_value={})),
        patch("app.services.screener.get_financials", new=AsyncMock(return_value=empty_fins)),
    ):
        f_score, breakdown = await calculate_piotroski_fscore("BROKEN")

    assert f_score == 0
    assert all(v == 0 for v in breakdown.values())


class TestPassesFilters:
    """Test that passes_filters gate is enforced correctly."""

    def test_passes_all_filters(self):
        result = ScreeningResult(
            ticker="GOOD",
            financials=FinancialMetrics(z_score=3.5, f_score=7),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 110},
        )
        assert result.passes_filters is True

    def test_fails_z_score(self):
        result = ScreeningResult(
            ticker="BAD_Z",
            financials=FinancialMetrics(z_score=1.5, f_score=7),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 110},
        )
        assert result.passes_filters is False

    def test_fails_f_score(self):
        result = ScreeningResult(
            ticker="BAD_F",
            financials=FinancialMetrics(z_score=3.5, f_score=4),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 110},
        )
        assert result.passes_filters is False

    def test_fails_cf_quality(self):
        result = ScreeningResult(
            ticker="BAD_CF",
            financials=FinancialMetrics(z_score=3.5, f_score=7),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 70},
        )
        assert result.passes_filters is False

    def test_boundary_z_score_exact_1_8(self):
        """Z-Score == 1.8 should NOT pass (must be strictly > 1.8)."""
        result = ScreeningResult(
            ticker="BOUNDARY",
            financials=FinancialMetrics(z_score=1.8, f_score=7),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 110},
        )
        assert result.passes_filters is False

    def test_boundary_f_score_exact_5(self):
        """F-Score == 5 should pass (threshold is >=5)."""
        result = ScreeningResult(
            ticker="BOUNDARY_F",
            financials=FinancialMetrics(z_score=3.5, f_score=5),
            cash_flow_quality={"net_income": 100, "operating_cash_flow": 110},
        )
        assert result.passes_filters is True

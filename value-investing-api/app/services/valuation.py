"""
DCF Multi-Scenario Valuation Service.

Three scenarios (Pessimistic / Base / Optimistic) weighted 30/40/30.
WACC is computed via CAPM using the beta from yfinance and a configurable
risk-free rate from environment variables.

Gordon Growth Model terminal value:
  TV = FCF_n * (1 + g) / (WACC - g)
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from app.models.schemas import DCFScenario, ValuationData
from app.utils.financial_data import get_financials, get_info, safe_get, safe_get_multi

logger = logging.getLogger(__name__)

RISK_FREE_RATE = float(os.getenv("RISK_FREE_RATE", "0.045"))
MARKET_RETURN = 0.10  # Long-run equity market return assumption
SAFETY_MARGIN_THRESHOLD = 0.30  # 30% required margin of safety
PROJECTION_YEARS = 10

SCENARIOS = [
    {
        "name": "pessimistic",
        "growth_rate_multiplier": 0.5,
        "terminal_growth": 0.02,
        "wacc_spread": 0.02,
        "weight": 0.30,
    },
    {
        "name": "base",
        "growth_rate_multiplier": 1.0,
        "terminal_growth": 0.025,
        "wacc_spread": 0.0,
        "weight": 0.40,
    },
    {
        "name": "optimistic",
        "growth_rate_multiplier": 1.5,
        "terminal_growth": 0.03,
        "wacc_spread": -0.01,
        "weight": 0.30,
    },
]


def _compute_wacc(beta: float, cost_of_debt: float, tax_rate: float, d_ratio: float) -> float:
    cost_of_equity = RISK_FREE_RATE + beta * (MARKET_RETURN - RISK_FREE_RATE)
    e_ratio = 1.0 - d_ratio
    wacc = e_ratio * cost_of_equity + d_ratio * cost_of_debt * (1 - tax_rate)
    return max(wacc, 0.06)  # floor at 6% to avoid unrealistic valuations


def _project_fcf(base_fcf: float, growth_rate: float, years: int) -> List[float]:
    fcfs = []
    fcf = base_fcf
    for _ in range(years):
        fcf = fcf * (1 + growth_rate)
        fcfs.append(fcf)
    return fcfs


def _dcf_value(fcfs: List[float], terminal_value: float, wacc: float) -> float:
    pv = 0.0
    for t, fcf in enumerate(fcfs, start=1):
        pv += fcf / (1 + wacc) ** t
    pv += terminal_value / (1 + wacc) ** len(fcfs)
    return pv


async def calculate_dcf_valuation(ticker: str) -> ValuationData:
    valuation = ValuationData()
    try:
        import asyncio
        info, fins = await asyncio.gather(get_info(ticker), get_financials(ticker))

        current_price: Optional[float] = info.get("currentPrice") or info.get("regularMarketPrice")
        shares_outstanding: Optional[float] = info.get("sharesOutstanding")
        beta: Optional[float] = info.get("beta") or 1.0
        beta = max(0.5, min(float(beta), 3.0))  # clamp to sensible range

        cf = fins.get("cashflow")
        bs = fins.get("balance_sheet")
        inc = fins.get("income_stmt")

        # Free Cash Flow = Operating CF - CapEx
        ocf_vals = safe_get_multi(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"], 3)
        capex_vals = safe_get_multi(cf, ["Capital Expenditure", "Purchase Of Property Plant And Equipment"], 3)

        if not ocf_vals or ocf_vals[0] is None:
            logger.warning("%s: No OCF data for DCF", ticker)
            return valuation

        ocf_now = ocf_vals[0]
        capex_now = abs(capex_vals[0]) if capex_vals and capex_vals[0] is not None else 0.0
        base_fcf = ocf_now - capex_now

        if base_fcf <= 0:
            logger.warning("%s: Negative base FCF (%.0f), DCF not meaningful", ticker, base_fcf)
            valuation.current_price = current_price
            return valuation

        # Estimate FCF growth from trailing 3-year CAGR if available
        if len(ocf_vals) >= 3 and ocf_vals[2] and ocf_vals[2] > 0:
            capex_2 = abs(capex_vals[2]) if len(capex_vals) >= 3 and capex_vals[2] is not None else 0.0
            fcf_2y_ago = ocf_vals[2] - capex_2
            if fcf_2y_ago > 0:
                hist_cagr = (base_fcf / fcf_2y_ago) ** (1 / 2) - 1
                base_growth = max(-0.1, min(hist_cagr, 0.35))  # clamp -10%..35%
            else:
                base_growth = 0.05
        else:
            base_growth = float(info.get("revenueGrowth") or 0.05)
            base_growth = max(0.01, min(base_growth, 0.35))

        # Cost of debt approximation
        total_debt = safe_get(bs, ["Total Debt", "Long Term Debt"]) or 0.0
        interest_expense = abs(safe_get(inc, ["Interest Expense"]) or 0.0)
        cost_of_debt = (interest_expense / total_debt if total_debt > 0 else 0.04)
        cost_of_debt = max(0.03, min(cost_of_debt, 0.15))

        # D/E ratio → D/V
        equity = info.get("marketCap") or 1.0
        d_v_ratio = total_debt / (total_debt + equity) if (total_debt + equity) > 0 else 0.2
        d_v_ratio = min(d_v_ratio, 0.80)

        base_wacc = _compute_wacc(beta, cost_of_debt, 0.21, d_v_ratio)

        scenario_results: List[DCFScenario] = []
        weighted_fair_value = 0.0

        for s in SCENARIOS:
            growth = base_growth * s["growth_rate_multiplier"]
            wacc = base_wacc + s["wacc_spread"]
            wacc = max(wacc, 0.05)
            terminal_g = s["terminal_growth"]

            fcfs = _project_fcf(base_fcf, growth, PROJECTION_YEARS)
            terminal_fcf = fcfs[-1] * (1 + terminal_g)
            if wacc <= terminal_g:
                terminal_value = terminal_fcf / 0.05  # fallback
            else:
                terminal_value = terminal_fcf / (wacc - terminal_g)

            total_pv = _dcf_value(fcfs, terminal_value, wacc)

            # Per-share value
            if shares_outstanding and shares_outstanding > 0:
                per_share = total_pv / shares_outstanding
            else:
                per_share = 0.0

            scenario_results.append(
                DCFScenario(
                    name=s["name"],
                    growth_rate=round(growth, 4),
                    terminal_growth_rate=terminal_g,
                    wacc=round(wacc, 4),
                    fair_value=round(per_share, 2),
                    weight=s["weight"],
                )
            )
            weighted_fair_value += per_share * s["weight"]

        fair_value = round(weighted_fair_value, 2)
        safety_margin = (
            round((fair_value - current_price) / fair_value * 100, 2)
            if current_price and fair_value > 0
            else None
        )

        valuation = ValuationData(
            current_price=current_price,
            fair_value_dcf=fair_value,
            safety_margin=safety_margin,
            scenarios=scenario_results,
        )

    except Exception as exc:
        logger.error("DCF calculation failed for %s: %s", ticker, exc, exc_info=True)

    return valuation

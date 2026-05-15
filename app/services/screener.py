"""
Altman Z-Score and Piotroski F-Score calculation service.

Altman Z-Score (1968 public company model):
  Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
  X1 = Working Capital / Total Assets
  X2 = Retained Earnings / Total Assets
  X3 = EBIT / Total Assets
  X4 = Market Cap / Total Liabilities
  X5 = Revenue / Total Assets

Zones: Safe > 2.99, Grey 1.81–2.99, Distress < 1.81
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from app.models.schemas import FinancialMetrics
from app.utils.financial_data import get_financials, get_info, safe_get, safe_get_multi

logger = logging.getLogger(__name__)


async def calculate_altman_z_score(ticker: str) -> FinancialMetrics:
    metrics = FinancialMetrics()
    try:
        info, fins = await _fetch_data(ticker)
        bs = fins.get("balance_sheet")
        inc = fins.get("income_stmt")

        total_assets = safe_get(bs, ["Total Assets"])
        if not total_assets or total_assets == 0:
            logger.warning("%s: Total Assets unavailable for Z-Score", ticker)
            return metrics

        # X1 — Working Capital / Total Assets
        current_assets = safe_get(bs, ["Current Assets", "Total Current Assets"])
        current_liabilities = safe_get(
            bs, ["Current Liabilities", "Total Current Liabilities"]
        )
        working_capital = (
            (current_assets or 0) - (current_liabilities or 0)
            if current_assets is not None and current_liabilities is not None
            else None
        )
        x1 = working_capital / total_assets if working_capital is not None else None

        # X2 — Retained Earnings / Total Assets
        retained_earnings = safe_get(bs, ["Retained Earnings"])
        x2 = retained_earnings / total_assets if retained_earnings is not None else None

        # X3 — EBIT / Total Assets
        ebit = safe_get(inc, ["EBIT", "Operating Income"])
        x3 = ebit / total_assets if ebit is not None else None

        # X4 — Market Cap / Total Liabilities
        market_cap = info.get("marketCap")
        total_liabilities = safe_get(
            bs, ["Total Liabilities Net Minority Interest", "Total Liabilities"]
        )
        x4 = (
            market_cap / total_liabilities
            if market_cap and total_liabilities and total_liabilities != 0
            else None
        )

        # X5 — Revenue / Total Assets
        revenue = safe_get(inc, ["Total Revenue"])
        x5 = revenue / total_assets if revenue is not None else None

        # Assemble score using available components (substitute 0 only for missing)
        components = {"x1": x1, "x2": x2, "x3": x3, "x4": x4, "x5": x5}
        weights = {"x1": 1.2, "x2": 1.4, "x3": 3.3, "x4": 0.6, "x5": 1.0}

        available = {k: v for k, v in components.items() if v is not None}
        if len(available) < 3:
            logger.warning("%s: Insufficient data for Z-Score (%d/5 components)", ticker, len(available))
            return metrics

        z_score = sum(weights[k] * v for k, v in available.items())
        metrics.z_score = round(z_score, 4)
        metrics.z_score_components = {k: round(v, 6) if v is not None else None for k, v in components.items()}

        # Additional ratios while we have the data
        if current_assets and current_liabilities and current_liabilities != 0:
            metrics.current_ratio = round(current_assets / current_liabilities, 4)
        if total_assets:
            equity = safe_get(bs, ["Stockholders Equity", "Total Stockholder Equity"])
            if equity and equity != 0:
                metrics.roa = round((ebit or 0) / total_assets, 4)
                metrics.roe = round((ebit or 0) / equity, 4)
                total_debt = safe_get(bs, ["Total Debt", "Long Term Debt"])
                if total_debt is not None:
                    metrics.debt_to_equity = round(total_debt / equity, 4)
        if revenue and total_assets:
            metrics.asset_turnover = round(revenue / total_assets, 4)

        metrics.pe_ratio = info.get("trailingPE")
        metrics.pb_ratio = info.get("priceToBook")
        gross_profit = safe_get(inc, ["Gross Profit"])
        if gross_profit and revenue:
            metrics.gross_margin = round(gross_profit / revenue, 4)

    except Exception as exc:
        logger.error("Z-Score calculation failed for %s: %s", ticker, exc, exc_info=True)

    return metrics


async def calculate_piotroski_fscore(ticker: str) -> Tuple[int, dict]:
    """
    Returns (f_score: int, breakdown: dict) — 9 binary criteria.
    breakdown maps criterion name -> 0 or 1.
    """
    breakdown = {
        "roa_positive": 0,
        "ocf_positive": 0,
        "delta_roa_positive": 0,
        "ocf_gt_net_income": 0,
        "delta_leverage_negative": 0,
        "delta_liquidity_positive": 0,
        "no_dilution": 0,
        "delta_gross_margin_positive": 0,
        "delta_asset_turnover_positive": 0,
    }
    try:
        _, fins = await _fetch_data(ticker)
        bs = fins.get("balance_sheet")
        inc = fins.get("income_stmt")
        cf = fins.get("cashflow")

        # Current year and prior year helpers
        total_assets_vals = safe_get_multi(bs, ["Total Assets"], 2)
        if len(total_assets_vals) < 1 or not total_assets_vals[0]:
            return None, breakdown

        ta_now = total_assets_vals[0]
        ta_prev = total_assets_vals[1] if len(total_assets_vals) > 1 else None

        net_income_now = safe_get(inc, ["Net Income", "Net Income Common Stockholders"])
        net_income_prev = safe_get(inc, ["Net Income", "Net Income Common Stockholders"], col_index=1)
        ocf_now = safe_get(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        ocf_prev = safe_get(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"], col_index=1)

        # Long-term debt ratios
        ltd_now = safe_get(bs, ["Long Term Debt"])
        ltd_prev = safe_get(bs, ["Long Term Debt"], col_index=1)
        leverage_now = ltd_now / ta_now if ltd_now is not None else None
        leverage_prev = (ltd_prev / ta_prev if ltd_prev is not None and ta_prev else None)

        # Current ratio
        ca_now = safe_get(bs, ["Current Assets", "Total Current Assets"])
        cl_now = safe_get(bs, ["Current Liabilities", "Total Current Liabilities"])
        ca_prev = safe_get(bs, ["Current Assets", "Total Current Assets"], col_index=1)
        cl_prev = safe_get(bs, ["Current Liabilities", "Total Current Liabilities"], col_index=1)
        cr_now = ca_now / cl_now if ca_now and cl_now and cl_now != 0 else None
        cr_prev = ca_prev / cl_prev if ca_prev and cl_prev and cl_prev != 0 else None

        # Shares outstanding
        shares_now = safe_get(bs, ["Ordinary Shares Number", "Common Stock"])
        shares_prev = safe_get(bs, ["Ordinary Shares Number", "Common Stock"], col_index=1)

        # Gross margin
        gp_now = safe_get(inc, ["Gross Profit"])
        rev_now = safe_get(inc, ["Total Revenue"])
        gp_prev = safe_get(inc, ["Gross Profit"], col_index=1)
        rev_prev = safe_get(inc, ["Total Revenue"], col_index=1)
        gm_now = gp_now / rev_now if gp_now and rev_now else None
        gm_prev = gp_prev / rev_prev if gp_prev and rev_prev else None

        # Asset turnover
        at_now = rev_now / ta_now if rev_now else None
        at_prev = rev_prev / ta_prev if rev_prev and ta_prev else None

        # ROA
        roa_now = net_income_now / ta_now if net_income_now is not None else None
        roa_prev = (
            net_income_prev / ta_prev
            if net_income_prev is not None and ta_prev
            else None
        )

        # --- Profitability (4 criteria) ---
        if roa_now is not None and roa_now > 0:
            breakdown["roa_positive"] = 1
        if ocf_now is not None and ocf_now > 0:
            breakdown["ocf_positive"] = 1
        if roa_now is not None and roa_prev is not None and roa_now > roa_prev:
            breakdown["delta_roa_positive"] = 1
        if ocf_now is not None and net_income_now is not None and ocf_now > net_income_now:
            breakdown["ocf_gt_net_income"] = 1

        # --- Leverage / Liquidity (3 criteria) ---
        if leverage_now is not None and leverage_prev is not None and leverage_now < leverage_prev:
            breakdown["delta_leverage_negative"] = 1
        if cr_now is not None and cr_prev is not None and cr_now > cr_prev:
            breakdown["delta_liquidity_positive"] = 1
        if shares_now is not None and shares_prev is not None and shares_now <= shares_prev:
            breakdown["no_dilution"] = 1

        # --- Efficiency (2 criteria) ---
        if gm_now is not None and gm_prev is not None and gm_now > gm_prev:
            breakdown["delta_gross_margin_positive"] = 1
        if at_now is not None and at_prev is not None and at_now > at_prev:
            breakdown["delta_asset_turnover_positive"] = 1

    except Exception as exc:
        logger.error("F-Score calculation failed for %s: %s", ticker, exc, exc_info=True)

    f_score = sum(breakdown.values())
    return f_score, breakdown


async def _fetch_data(ticker: str):
    import asyncio
    info_task = get_info(ticker)
    fins_task = get_financials(ticker)
    info, fins = await asyncio.gather(info_task, fins_task)
    return info, fins

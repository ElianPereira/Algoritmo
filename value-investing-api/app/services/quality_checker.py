"""
Cash Flow Quality Checker — detects value traps where reported earnings
are not backed by actual operating cash flows.

Rule: OCF / Net Income > 0.8 required to pass filters.
A ratio < 0.8 flags a potential value trap (earnings manipulation risk).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.models.schemas import CashFlowQuality
from app.utils.financial_data import get_financials, safe_get, safe_get_multi

logger = logging.getLogger(__name__)


async def check_cash_flow_quality(ticker: str) -> CashFlowQuality:
    try:
        fins = await get_financials(ticker)
        cf = fins.get("cashflow")
        inc = fins.get("income_stmt")

        net_income = safe_get(inc, ["Net Income", "Net Income Common Stockholders"])
        ocf = safe_get(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])

        # Last 3 years history
        ni_vals = safe_get_multi(inc, ["Net Income", "Net Income Common Stockholders"], 3)
        ocf_vals = safe_get_multi(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"], 3)

        history: List[dict] = []
        for i in range(min(len(ni_vals), len(ocf_vals))):
            ni = ni_vals[i]
            op = ocf_vals[i]
            ratio = round(op / ni, 4) if ni and ni != 0 else None
            history.append({"year_offset": i, "net_income": ni, "operating_cash_flow": op, "quality_ratio": ratio})

        quality = CashFlowQuality(
            net_income=net_income,
            operating_cash_flow=ocf,
            history=history if history else None,
        )
        return quality

    except Exception as exc:
        logger.error("Cash flow quality check failed for %s: %s", ticker, exc, exc_info=True)
        return CashFlowQuality()

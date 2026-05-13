"""
Telegram alert service.

Sends a formatted message to a Telegram chat when a screened stock
crosses the alert threshold (default: Z-Score > 3.0).

Required env vars:
  TELEGRAM_BOT_TOKEN  — BotFather token
  TELEGRAM_CHAT_ID    — numeric chat / channel ID

If these are not set the service silently skips sending (no crash).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from app.models.schemas import RiskLevel, ScreeningResult

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
Z_SCORE_ALERT_THRESHOLD = float(os.getenv("Z_SCORE_ALERT_THRESHOLD", "3.0"))
F_SCORE_ALERT_THRESHOLD = int(os.getenv("F_SCORE_ALERT_THRESHOLD", "7"))


def _build_message(result: ScreeningResult) -> str:
    fin = result.financials
    val = result.valuation
    cfq = result.cash_flow_quality

    risk_emoji = {
        RiskLevel.safe: "🟢",
        RiskLevel.grey_zone: "🟡",
        RiskLevel.distress: "🔴",
        RiskLevel.unknown: "⚪",
    }.get(result.risk_level, "⚪")

    lines = [
        f"📊 *Value Investing Alert*",
        f"",
        f"{risk_emoji} *{result.ticker}* — {result.company_name or 'N/A'}",
        f"",
        f"*Financial Health*",
        f"• Altman Z-Score: `{fin.z_score:.2f}`  _(threshold > {Z_SCORE_ALERT_THRESHOLD})_",
        f"• Piotroski F-Score: `{fin.f_score}/9`",
        f"• CF Quality Ratio: `{cfq.quality_ratio:.2f}`",
        f"",
        f"*Valuation*",
        f"• Current Price:  `${val.current_price:.2f}`" if val.current_price else "• Current Price: N/A",
        f"• Fair Value DCF: `${val.fair_value_dcf:.2f}`" if val.fair_value_dcf else "• Fair Value DCF: N/A",
        f"• Upside:         `{val.upside_potential:+.1f}%`" if val.upside_potential is not None else "• Upside: N/A",
        f"",
        f"✅ *Passes all filters: {'YES' if result.passes_filters else 'NO'}*",
    ]

    if result.downside_risks:
        lines += ["", "⚠️ *Key Risks*"]
        for risk in result.downside_risks[:3]:
            lines.append(f"• {risk}")

    return "\n".join(lines)


async def send_alert(result: ScreeningResult) -> bool:
    """
    Send a Telegram alert for a screening result.
    Returns True if the message was sent, False otherwise.
    Skips silently if credentials are not configured.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.debug("Telegram credentials not configured — skipping alert for %s", result.ticker)
        return False

    message = _build_message(result)
    url = TELEGRAM_API.format(token=token)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            })
            resp.raise_for_status()
            logger.info("Telegram alert sent for %s", result.ticker)
            return True
    except Exception as exc:
        logger.warning("Failed to send Telegram alert for %s: %s", result.ticker, exc)
        return False


def should_alert(result: ScreeningResult) -> bool:
    """
    Determine whether a screening result warrants a Telegram alert.
    Triggers when Z-Score > threshold AND F-Score >= threshold AND passes all filters.
    """
    fin = result.financials
    z_ok = fin.z_score is not None and fin.z_score > Z_SCORE_ALERT_THRESHOLD
    f_ok = fin.f_score is not None and fin.f_score >= F_SCORE_ALERT_THRESHOLD
    return z_ok and f_ok and result.passes_filters


async def maybe_send_alert(result: ScreeningResult) -> None:
    """Fire-and-forget: send alert only if the result meets alert criteria."""
    if should_alert(result):
        await send_alert(result)

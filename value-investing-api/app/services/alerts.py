"""
WhatsApp alert service via Twilio.

Sends a WhatsApp message when a screened stock crosses the alert threshold
(default: Z-Score > 3.0 AND F-Score >= 7 AND passes_filters=True).

Required env vars:
  TWILIO_ACCOUNT_SID   — Twilio Account SID (starts with AC...)
  TWILIO_AUTH_TOKEN    — Twilio Auth Token
  TWILIO_FROM_WHATSAPP — Sender number, e.g. whatsapp:+14155238886
  TWILIO_TO_WHATSAPP   — Recipient number, e.g. whatsapp:+521234567890

If these are not set the service silently skips sending (no crash).

Setup:
  1. Create a Twilio account at https://www.twilio.com
  2. Enable the WhatsApp Sandbox (or use an approved WhatsApp Business number)
  3. Add the four env vars above to your .env file
"""
from __future__ import annotations

import logging
import os

import httpx

from app.models.schemas import RiskLevel, ScreeningResult

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
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

    price_line = f"• Precio actual:  ${val.current_price:.2f}" if val.current_price else "• Precio actual: N/A"
    fv_line = f"• Valor justo DCF: ${val.fair_value_dcf:.2f}" if val.fair_value_dcf else "• Valor justo DCF: N/A"
    upside_line = f"• Potencial alza: {val.upside_potential:+.1f}%" if val.upside_potential is not None else "• Potencial alza: N/A"

    lines = [
        "📊 *Value Investing Alert*",
        "",
        f"{risk_emoji} *{result.ticker}* — {result.company_name or 'N/A'}",
        "",
        "*Salud Financiera*",
        f"• Altman Z-Score: {fin.z_score:.2f}  (umbral > {Z_SCORE_ALERT_THRESHOLD})",
        f"• Piotroski F-Score: {fin.f_score}/9",
        f"• CF Quality Ratio: {cfq.quality_ratio:.2f}",
        "",
        "*Valuación*",
        price_line,
        fv_line,
        upside_line,
        "",
        f"✅ Pasa todos los filtros: {'SÍ' if result.passes_filters else 'NO'}",
    ]

    if result.downside_risks:
        lines += ["", "⚠️ *Riesgos clave*"]
        for risk in result.downside_risks[:3]:
            lines.append(f"• {risk}")

    return "\n".join(lines)


async def send_alert(result: ScreeningResult) -> bool:
    """
    Send a WhatsApp message via Twilio for a screening result.
    Returns True if sent successfully, False otherwise.
    Skips silently if credentials are not configured.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_WHATSAPP")
    to_number = os.getenv("TWILIO_TO_WHATSAPP")

    if not all([account_sid, auth_token, from_number, to_number]):
        logger.debug("Twilio credentials not configured — skipping WhatsApp alert for %s", result.ticker)
        return False

    url = TWILIO_API.format(account_sid=account_sid)
    message = _build_message(result)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(account_sid, auth_token),
                data={
                    "From": from_number,
                    "To": to_number,
                    "Body": message,
                },
            )
            resp.raise_for_status()
            sid = resp.json().get("sid", "unknown")
            logger.info("WhatsApp alert sent for %s (SID: %s)", result.ticker, sid)
            return True
    except Exception as exc:
        logger.warning("Failed to send WhatsApp alert for %s: %s", result.ticker, exc)
        return False


def should_alert(result: ScreeningResult) -> bool:
    """
    Determine whether a result warrants a WhatsApp alert.
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

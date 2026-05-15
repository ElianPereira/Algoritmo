"""
Email alert service via Gmail SMTP (aiosmtplib).

Sends an HTML email when a screened stock crosses the alert threshold
(default: Z-Score > 3.0 AND F-Score >= 7 AND passes_filters=True).

Required env vars:
  SMTP_USER      — Gmail address (sender and recipient)
  SMTP_PASSWORD  — Gmail App Password (NOT your account password)
                   Generate at: https://myaccount.google.com/apppasswords

If SMTP_USER / SMTP_PASSWORD are not set the service silently skips (no crash).
"""
from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings
from app.models.schemas import RiskLevel, ScreeningResult

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
Z_SCORE_ALERT_THRESHOLD = settings.z_score_alert_threshold
F_SCORE_ALERT_THRESHOLD = settings.f_score_alert_threshold


def _risk_color(risk: RiskLevel) -> str:
    return {
        RiskLevel.safe: "#27AE60",
        RiskLevel.grey_zone: "#F39C12",
        RiskLevel.distress: "#E74C3C",
    }.get(risk, "#95A5A6")


def _fmt(val, prefix="", suffix="", decimals=2):
    if val is None:
        return "N/A"
    try:
        return f"{prefix}{float(val):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _build_html(result: ScreeningResult) -> str:
    fin = result.financials
    val = result.valuation
    cfq = result.cash_flow_quality
    color = _risk_color(result.risk_level)
    badge = result.risk_level.value.upper()
    filter_badge = ("✅ PASA FILTROS" if result.passes_filters else "❌ NO PASA FILTROS")
    filter_color = "#27AE60" if result.passes_filters else "#E74C3C"

    risks_html = "".join(
        f"<li style='color:#E74C3C;margin:4px 0'>{r}</li>"
        for r in (result.downside_risks or [])[:5]
    ) or "<li>No se identificaron riesgos específicos.</li>"

    scenarios_html = ""
    if val.scenarios:
        rows = "".join(
            f"""<tr style='background:{"#f9f9f9" if i%2 else "white"}'>
                <td>{s.name.capitalize()}</td>
                <td>{s.growth_rate*100:.1f}%</td>
                <td>{s.wacc*100:.1f}%</td>
                <td><b>${s.fair_value:,.2f}</b></td>
                <td>{s.weight*100:.0f}%</td>
            </tr>"""
            for i, s in enumerate(val.scenarios)
        )
        scenarios_html = f"""
        <h3 style='color:#1B3A6B;border-bottom:1px solid #ddd;padding-bottom:4px'>Escenarios DCF</h3>
        <table width='100%' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
            <tr style='background:#1B3A6B;color:white'>
                <th>Escenario</th><th>Crec.</th><th>WACC</th><th>Valor Justo</th><th>Peso</th>
            </tr>
            {rows}
        </table>"""

    return f"""<!DOCTYPE html>
<html>
<body style='font-family:Arial,sans-serif;max-width:620px;margin:0 auto;color:#333'>
  <div style='background:#1B3A6B;padding:20px 24px;border-radius:8px 8px 0 0'>
    <h2 style='color:white;margin:0'>📊 Value Investing Alert</h2>
    <p style='color:#AED6F1;margin:6px 0 0'>Análisis automático — Factor Investing</p>
  </div>

  <div style='background:#fff;padding:24px;border:1px solid #ddd;border-top:none'>

    <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:16px'>
      <div>
        <h2 style='margin:0;color:#1B3A6B'>{result.ticker}</h2>
        <p style='margin:2px 0;color:#555'>{result.company_name or "N/A"}</p>
      </div>
      <div style='text-align:right'>
        <span style='background:{color};color:white;padding:4px 12px;border-radius:20px;font-weight:bold;font-size:13px'>{badge}</span><br>
        <span style='color:{filter_color};font-weight:bold;font-size:13px;margin-top:6px;display:inline-block'>{filter_badge}</span>
      </div>
    </div>

    <h3 style='color:#1B3A6B;border-bottom:1px solid #ddd;padding-bottom:4px'>Salud Financiera</h3>
    <table width='100%' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
      <tr style='background:#EBF5FB'>
        <td><b>Altman Z-Score</b></td>
        <td><b style='font-size:16px'>{_fmt(fin.z_score, decimals=2)}</b></td>
        <td style='color:#888'>umbral &gt; {Z_SCORE_ALERT_THRESHOLD}</td>
      </tr>
      <tr>
        <td><b>Piotroski F-Score</b></td>
        <td><b style='font-size:16px'>{fin.f_score}/9</b></td>
        <td style='color:#888'>umbral ≥ {F_SCORE_ALERT_THRESHOLD}</td>
      </tr>
      <tr style='background:#EBF5FB'>
        <td><b>CF Quality Ratio</b></td>
        <td><b style='font-size:16px'>{_fmt(cfq.quality_ratio, decimals=2)}</b></td>
        <td style='color:#888'>umbral &gt; 0.8</td>
      </tr>
      <tr>
        <td><b>P/E Ratio</b></td>
        <td>{_fmt(fin.pe_ratio, decimals=1)}</td><td></td>
      </tr>
      <tr style='background:#EBF5FB'>
        <td><b>Deuda / Capital</b></td>
        <td>{_fmt(fin.debt_to_equity, decimals=2)}</td><td></td>
      </tr>
    </table>

    <h3 style='color:#1B3A6B;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:20px'>Valuación</h3>
    <table width='100%' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
      <tr style='background:#EBF5FB'>
        <td><b>Precio Actual</b></td>
        <td><b style='font-size:16px'>{_fmt(val.current_price, prefix="$")}</b></td>
      </tr>
      <tr>
        <td><b>Valor Justo DCF</b></td>
        <td><b style='font-size:16px'>{_fmt(val.fair_value_dcf, prefix="$")}</b></td>
      </tr>
      <tr style='background:#EBF5FB'>
        <td><b>Margen de Seguridad</b></td>
        <td><b style='font-size:16px'>{_fmt(val.safety_margin, suffix="%")}</b></td>
      </tr>
      <tr>
        <td><b>Potencial Alza</b></td>
        <td><b style='font-size:16px;color:{"#27AE60" if (val.upside_potential or 0) >= 0 else "#E74C3C"}'>{_fmt(val.upside_potential, suffix="%")}</b></td>
      </tr>
    </table>

    {scenarios_html}

    <h3 style='color:#1B3A6B;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:20px'>⚠️ Riesgos Clave</h3>
    <ul style='margin:8px 0;padding-left:20px'>{risks_html}</ul>

  </div>

  <div style='background:#F2F3F4;padding:12px 24px;border-radius:0 0 8px 8px;font-size:11px;color:#888;border:1px solid #ddd;border-top:none'>
    Este reporte es informativo. No constituye asesoría financiera.
    Horizonte mínimo de inversión: 6 meses.
    Generado automáticamente por Value Investing API.
  </div>
</body>
</html>"""


def _build_plain(result: ScreeningResult) -> str:
    fin = result.financials
    val = result.valuation
    cfq = result.cash_flow_quality
    lines = [
        "VALUE INVESTING ALERT",
        f"{result.ticker} — {result.company_name or 'N/A'}",
        f"Riesgo: {result.risk_level.value.upper()}",
        f"Pasa filtros: {'SÍ' if result.passes_filters else 'NO'}",
        "",
        "SALUD FINANCIERA",
        f"  Altman Z-Score : {_fmt(fin.z_score, decimals=2)}",
        f"  Piotroski F-Score: {fin.f_score}/9",
        f"  CF Quality Ratio : {_fmt(cfq.quality_ratio, decimals=2)}",
        "",
        "VALUACIÓN",
        f"  Precio actual  : {_fmt(val.current_price, prefix='$')}",
        f"  Valor justo DCF: {_fmt(val.fair_value_dcf, prefix='$')}",
        f"  Potencial alza : {_fmt(val.upside_potential, suffix='%')}",
    ]
    if result.downside_risks:
        lines += ["", "RIESGOS"] + [f"  • {r}" for r in result.downside_risks[:5]]
    return "\n".join(lines)


async def send_alert(result: ScreeningResult) -> bool:
    """
    Send an email alert via Gmail SMTP.
    Returns True if sent, False otherwise.
    Skips silently if credentials are not configured.
    """
    smtp_user = settings.smtp_user
    smtp_password = settings.smtp_password

    if not smtp_password:
        logger.debug("SMTP_PASSWORD not configured — skipping email alert for %s", result.ticker)
        return False

    subject = (
        f"📊 [{result.ticker}] Value Investing Alert — "
        f"Z-Score {_fmt(result.financials.z_score, decimals=2)} | "
        f"F-Score {result.financials.f_score}/9"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_user
    msg.attach(MIMEText(_build_plain(result), "plain"))
    msg.attach(MIMEText(_build_html(result), "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=smtp_user,
            password=smtp_password,
            start_tls=True,
        )
        logger.info("Email alert sent for %s to %s", result.ticker, smtp_user)
        return True
    except Exception as exc:
        logger.warning("Failed to send email alert for %s: %s", result.ticker, exc)
        return False


def should_alert(result: ScreeningResult) -> bool:
    fin = result.financials
    z_ok = fin.z_score is not None and fin.z_score > Z_SCORE_ALERT_THRESHOLD
    f_ok = fin.f_score is not None and fin.f_score >= F_SCORE_ALERT_THRESHOLD
    return z_ok and f_ok and result.passes_filters


async def maybe_send_alert(result: ScreeningResult) -> None:
    """Fire-and-forget: send alert only if the result meets alert criteria."""
    if should_alert(result):
        await send_alert(result)

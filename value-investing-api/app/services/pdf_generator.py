"""
PDF Investment Report Generator using ReportLab.

Sections:
  1. Executive Summary
  2. Valuation Table (DCF scenarios)
  3. Financial Health (Z-Score components)
  4. Cash Flow Quality (3-year OCF vs Net Income)
  5. Downside Risks
  6. Strategic Rationale
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.schemas import RiskLevel, ScreeningResult

PAGE_W, PAGE_H = A4
MARGIN = 2 * cm

# Colour palette
DARK_BLUE = colors.HexColor("#1B3A6B")
ACCENT = colors.HexColor("#2980B9")
GREEN = colors.HexColor("#27AE60")
RED = colors.HexColor("#E74C3C")
AMBER = colors.HexColor("#F39C12")
LIGHT_GREY = colors.HexColor("#F2F3F4")
MID_GREY = colors.HexColor("#BDC3C7")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20, textColor=DARK_BLUE, spaceAfter=6),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=11, textColor=ACCENT, spaceAfter=4),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontSize=13, textColor=DARK_BLUE, spaceBefore=12, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=10, leading=14),
        "risk_safe": ParagraphStyle("risk_safe", parent=base["Normal"], fontSize=11, textColor=GREEN),
        "risk_distress": ParagraphStyle("risk_distress", parent=base["Normal"], fontSize=11, textColor=RED),
        "risk_grey": ParagraphStyle("risk_grey", parent=base["Normal"], fontSize=11, textColor=AMBER),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontSize=10, leftIndent=12, leading=14),
    }


def _risk_colour(risk: RiskLevel) -> tuple:
    return {
        RiskLevel.safe: GREEN,
        RiskLevel.distress: RED,
        RiskLevel.grey_zone: AMBER,
    }.get(risk, MID_GREY)


def _fmt(val, prefix="", suffix="", decimals=2, na="N/A"):
    if val is None:
        return na
    try:
        return f"{prefix}{float(val):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return na


def generate_investment_report(result: ScreeningResult) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )
    s = _styles()
    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    story.append(Paragraph(f"Investment Analysis Report", s["title"]))
    story.append(Paragraph(
        f"{result.ticker} — {result.company_name or 'N/A'}  |  "
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        s["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=DARK_BLUE))
    story.append(Spacer(1, 0.3 * cm))

    # ── 1. Executive Summary ────────────────────────────────────────────────
    story.append(Paragraph("1. Executive Summary", s["section"]))

    risk_colour = _risk_colour(result.risk_level)
    badge_style = ParagraphStyle("badge", parent=s["body"], textColor=risk_colour)
    story.append(Paragraph(
        f"<b>Risk Level:</b> <b>{result.risk_level.value.upper()}</b>  |  "
        f"<b>Passes All Filters:</b> {'✓ YES' if result.passes_filters else '✗ NO'}",
        badge_style,
    ))
    story.append(Spacer(1, 0.2 * cm))

    fin = result.financials
    val = result.valuation

    exec_data = [
        ["Metric", "Value", "Threshold", "Pass?"],
        [
            "Altman Z-Score",
            _fmt(fin.z_score, decimals=2),
            "> 1.8",
            "✓" if (fin.z_score or 0) > 1.8 else "✗",
        ],
        [
            "Piotroski F-Score",
            str(fin.f_score) if fin.f_score is not None else "N/A",
            ">= 5",
            "✓" if (fin.f_score or 0) >= 5 else "✗",
        ],
        [
            "CF Quality Ratio",
            _fmt(result.cash_flow_quality.quality_ratio, decimals=2),
            "> 0.8",
            "✓" if (result.cash_flow_quality.quality_ratio or 0) > 0.8 else "✗",
        ],
        [
            "Upside Potential",
            _fmt(val.upside_potential, suffix="%"),
            "—",
            "—",
        ],
    ]
    story.append(_build_table(exec_data, col_widths=[7 * cm, 4 * cm, 3.5 * cm, 2 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 2. Valuation ────────────────────────────────────────────────────────
    story.append(Paragraph("2. DCF Valuation", s["section"]))
    val_data = [
        ["", "Value"],
        ["Current Price", _fmt(val.current_price, prefix="$")],
        ["Fair Value (DCF weighted)", _fmt(val.fair_value_dcf, prefix="$")],
        ["Safety Margin", _fmt(val.safety_margin, suffix="%")],
        ["Upside Potential", _fmt(val.upside_potential, suffix="%")],
    ]
    story.append(_build_table(val_data, col_widths=[9 * cm, 7.5 * cm]))

    if val.scenarios:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("<b>Scenario Breakdown</b>", s["body"]))
        sc_data = [["Scenario", "Growth", "WACC", "Terminal g", "Fair Value", "Weight"]]
        for sc in val.scenarios:
            sc_data.append([
                sc.name.capitalize(),
                _fmt(sc.growth_rate * 100, suffix="%"),
                _fmt(sc.wacc * 100, suffix="%"),
                _fmt(sc.terminal_growth_rate * 100, suffix="%"),
                _fmt(sc.fair_value, prefix="$"),
                _fmt(sc.weight * 100, suffix="%", decimals=0),
            ])
        story.append(_build_table(sc_data, col_widths=[3 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 3 * cm, 2.5 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 3. Financial Health (Z-Score Components) ────────────────────────────
    story.append(Paragraph("3. Financial Health — Altman Z-Score Components", s["section"]))
    if fin.z_score_components:
        weights = {"x1": 1.2, "x2": 1.4, "x3": 3.3, "x4": 0.6, "x5": 1.0}
        labels = {
            "x1": "Working Capital / Total Assets",
            "x2": "Retained Earnings / Total Assets",
            "x3": "EBIT / Total Assets",
            "x4": "Market Cap / Total Liabilities",
            "x5": "Revenue / Total Assets",
        }
        z_data = [["Component", "Description", "Raw Value", "Weight", "Contribution"]]
        for k, label in labels.items():
            raw = fin.z_score_components.get(k)
            w = weights[k]
            contrib = round(w * raw, 4) if raw is not None else None
            z_data.append([
                k.upper(),
                label,
                _fmt(raw, decimals=4),
                str(w),
                _fmt(contrib, decimals=4),
            ])
        z_data.append(["", "", "", "Z-SCORE", _fmt(fin.z_score, decimals=4)])
        story.append(_build_table(z_data, col_widths=[1.5 * cm, 6 * cm, 2.5 * cm, 2 * cm, 3.5 * cm]))
    else:
        story.append(Paragraph("Z-Score component data not available.", s["body"]))

    story.append(Spacer(1, 0.2 * cm))
    if fin.f_score_breakdown:
        story.append(Paragraph("<b>Piotroski F-Score Breakdown</b>", s["body"]))
        f_data = [["Criterion", "Result"]]
        readable = {
            "roa_positive": "ROA > 0",
            "ocf_positive": "Operating CF > 0",
            "delta_roa_positive": "ΔROA > 0 (vs prior year)",
            "ocf_gt_net_income": "OCF > Net Income",
            "delta_leverage_negative": "ΔLeverage < 0 (debt reduced)",
            "delta_liquidity_positive": "ΔCurrent Ratio > 0",
            "no_dilution": "No new share issuance",
            "delta_gross_margin_positive": "ΔGross Margin > 0",
            "delta_asset_turnover_positive": "ΔAsset Turnover > 0",
        }
        for key, label in readable.items():
            score = fin.f_score_breakdown.get(key, 0)
            f_data.append([label, "✓" if score else "✗"])
        f_data.append(["TOTAL F-SCORE", str(fin.f_score)])
        story.append(_build_table(f_data, col_widths=[12 * cm, 4.5 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 4. Cash Flow Quality ─────────────────────────────────────────────────
    story.append(Paragraph("4. Cash Flow Quality", s["section"]))
    cfq = result.cash_flow_quality
    cfq_data = [["Year", "Net Income", "Operating CF", "Quality Ratio", "Suspicious?"]]
    if cfq.history:
        for row in cfq.history:
            yr = f"T-{row['year_offset']}" if row["year_offset"] > 0 else "Current"
            susp = "⚠ YES" if (row.get("quality_ratio") or 1) < 0.8 else "NO"
            cfq_data.append([
                yr,
                _fmt(row.get("net_income"), prefix="$", decimals=0),
                _fmt(row.get("operating_cash_flow"), prefix="$", decimals=0),
                _fmt(row.get("quality_ratio"), decimals=2),
                susp,
            ])
    else:
        cfq_data.append(["N/A", "—", "—", "—", "—"])
    story.append(_build_table(cfq_data, col_widths=[2 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm, 3 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 5. Downside Risks ────────────────────────────────────────────────────
    story.append(Paragraph("5. Downside Risks", s["section"]))
    risk_style = ParagraphStyle("risk_item", parent=s["bullet"], textColor=RED)
    if result.downside_risks:
        for risk in result.downside_risks:
            story.append(Paragraph(f"• {risk}", risk_style))
    else:
        story.append(Paragraph("No specific risk factors identified.", s["body"]))
    story.append(Spacer(1, 0.4 * cm))

    # ── 6. Strategic Rationale ───────────────────────────────────────────────
    story.append(Paragraph("6. Strategic Rationale", s["section"]))
    story.append(_strategic_rationale(result, s))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY))
    story.append(Paragraph(
        "DISCLAIMER: This report is for informational purposes only and does not constitute "
        "financial advice. Investment horizon >= 6 months assumed. Past performance does not "
        "guarantee future results.",
        ParagraphStyle("disclaimer", parent=s["body"], fontSize=8, textColor=MID_GREY),
    ))

    doc.build(story)
    return buf.getvalue()


def _build_table(data, col_widths=None) -> Table:
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DARK_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("GRID", (0, 0), (-1, -1), 0.4, MID_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ])
    # Highlight last row as total if needed
    if data[-1][0] in ("", "Z-SCORE", "TOTAL F-SCORE"):
        style.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
        style.add("BACKGROUND", (0, -1), (-1, -1), LIGHT_GREY)
    t.setStyle(style)
    return t


def _strategic_rationale(result: ScreeningResult, s: dict) -> Paragraph:
    fin = result.financials
    val = result.valuation
    cfq = result.cash_flow_quality

    parts = []
    if result.passes_filters:
        parts.append(
            f"<b>{result.ticker}</b> passes all three value investing quality filters "
            f"(Z-Score {_fmt(fin.z_score, decimals=2)}, "
            f"F-Score {fin.f_score}/9, CF Quality {_fmt(cfq.quality_ratio, decimals=2)}), "
            f"indicating a financially healthy company with strong earnings quality."
        )
    else:
        reasons = []
        if fin.z_score is not None and fin.z_score <= 1.8:
            reasons.append(f"elevated financial distress risk (Z-Score {_fmt(fin.z_score, decimals=2)} ≤ 1.8)")
        if fin.f_score is not None and fin.f_score < 5:
            reasons.append(f"weak operational fundamentals (F-Score {fin.f_score}/9 < 5)")
        if cfq.quality_ratio is not None and cfq.quality_ratio <= 0.8:
            reasons.append(f"potential earnings quality concern (CF Ratio {_fmt(cfq.quality_ratio, decimals=2)} ≤ 0.8)")
        if reasons:
            parts.append(
                f"<b>{result.ticker}</b> does <b>not</b> pass all filters due to: "
                + "; ".join(reasons) + "."
            )
        else:
            parts.append(f"<b>{result.ticker}</b> did not pass all filters.")

    if val.upside_potential is not None:
        direction = "upside" if val.upside_potential >= 0 else "downside"
        parts.append(
            f"The DCF model suggests {abs(val.upside_potential):.1f}% {direction} to a "
            f"fair value of {_fmt(val.fair_value_dcf, prefix='$')} vs current price "
            f"{_fmt(val.current_price, prefix='$')}."
        )

    if fin.z_score is not None and fin.z_score > 2.99:
        parts.append(
            "The company operates in the <b>Safe Zone</b> of the Altman Z-Score model, "
            "suggesting low near-term bankruptcy risk."
        )

    return Paragraph(" ".join(parts), s["body"])

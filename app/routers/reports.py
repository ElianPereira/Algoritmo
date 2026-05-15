"""PDF report generation router."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.routers.screening import _run_full_analysis
from app.services.pdf_generator import generate_investment_report
from app.utils.validators import validate_ticker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/{ticker}/pdf", summary="Generate PDF investment report for a ticker")
async def generate_pdf_report(ticker: str) -> Response:
    """
    Runs a full analysis and returns a professional PDF investment report.
    """
    try:
        clean = validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        result = await _run_full_analysis(clean)
        pdf_bytes = generate_investment_report(result)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{clean}_analysis.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except Exception as exc:
        logger.error("PDF generation failed for %s: %s", clean, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}")

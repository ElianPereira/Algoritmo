"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import reports, screening

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Value Investing API",
    description=(
        "Automated Factor Investing analysis: Altman Z-Score, Piotroski F-Score, "
        "DCF multi-scenario valuation, and PDF report generation. "
        "Supports S&P 500 and Mexican BMV markets. "
        "Minimum investment horizon: 6 months (no day-trading support)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(screening.router)
app.include_router(reports.router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    return {"status": "ok", "version": "1.0.0"}


@app.get("/", tags=["Health"])
async def root() -> dict:
    return {
        "message": "Value Investing API",
        "docs": "/docs",
        "health": "/health",
        "example": "/screen/AAPL",
    }

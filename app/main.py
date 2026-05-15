"""FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db.session import async_session_factory, init_db
from app.routers import analytics, reports, screening
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    logger.info("Value Investing API started")
    yield
    stop_scheduler()
    logger.info("Value Investing API stopped")


app = FastAPI(
    title="Value Investing API",
    description=(
        "Automated Factor Investing analysis: Altman Z-Score, Piotroski F-Score, "
        "DCF multi-scenario valuation, PDF reports, and email alerts. "
        "NYSE + S&P 500 universe. Minimum investment horizon: 6 months."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(screening.router)
app.include_router(reports.router)
app.include_router(analytics.router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health/db", tags=["Health"])
async def db_health_check() -> dict:
    """Verify the database connection is working."""
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return {"status": "error", "database": "unreachable", "detail": str(exc)}


@app.get("/", tags=["Health"])
async def root() -> dict:
    return {
        "message": "Value Investing API",
        "docs": "/docs",
        "health": "/health",
        "db_health": "/health/db",
        "endpoints": {
            "screen_ticker": "POST /screen/{ticker}",
            "batch_screen": "POST /screen/batch",
            "top_opportunities": "GET /screen/opportunities/top",
            "pdf_report": "GET /reports/{ticker}/pdf",
            "history": "GET /analytics/history/{ticker}",
            "daily_summaries": "GET /analytics/summaries",
            "trigger_batch": "GET /analytics/trigger-batch",
        },
    }

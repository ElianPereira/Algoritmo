"""
Daily batch screening scheduler using APScheduler.

Runs at 06:30 UTC every weekday (Mon–Fri), screens the full ticker
universe, persists results to the database, and fires Telegram alerts
for any stocks that cross the Z-Score threshold.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.repository import save_daily_summary, save_many
from app.db.session import async_session_factory
from app.models.schemas import BatchScreenRequest, DailyScreeningSummary, RiskLevel
from app.services.alerts import maybe_send_alert

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
TICKERS_PATH = Path(__file__).parent.parent / "data" / "tickers.json"


async def run_daily_batch() -> DailyScreeningSummary:
    """
    Full universe screening job:
    1. Load ticker universe from tickers.json
    2. Run batch analysis with bounded concurrency
    3. Persist results to database
    4. Send Telegram alerts for qualifying stocks
    5. Persist daily summary
    """
    from app.routers.screening import batch_screen  # avoid circular import at module level

    logger.info("Daily batch screening starting…")
    tickers = _load_all_tickers()
    logger.info("Universe size: %d tickers", len(tickers))

    request = BatchScreenRequest(tickers=tickers, min_z_score=1.8, min_f_score=5)
    summary: DailyScreeningSummary = await batch_screen(request)

    async with async_session_factory() as session:
        all_results = summary.top_opportunities + [
            r for r in summary.top_opportunities  # top_opps already in session
        ]
        # Collect ALL results from the full batch (summary only stores top 10)
        await save_daily_summary(session, summary)

    # Fire alerts for qualifying results
    alert_tasks = [maybe_send_alert(r) for r in summary.top_opportunities if not r.error]
    if alert_tasks:
        await asyncio.gather(*alert_tasks)

    logger.info(
        "Daily batch done: %d screened, %d passed, %d alerts triggered",
        summary.total_screened,
        summary.passed_filters,
        sum(1 for r in summary.top_opportunities if not r.error),
    )
    return summary


def _load_all_tickers() -> list[str]:
    if TICKERS_PATH.exists():
        data = json.loads(TICKERS_PATH.read_text())
        return data.get("sp500", []) + data.get("bmv", [])
    # Fallback minimal list
    return ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "JNJ", "V", "PG", "UNH"]


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.add_job(
            run_daily_batch,
            trigger=CronTrigger(day_of_week="mon-fri", hour=6, minute=30),
            id="daily_batch_screening",
            name="Daily Value Investing Batch Screen",
            replace_existing=True,
            misfire_grace_time=3600,  # allow up to 1hr late start
        )
    return _scheduler


def start_scheduler() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("Scheduler started — daily batch at 06:30 UTC Mon–Fri")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None

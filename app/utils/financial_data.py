"""
yfinance wrapper with 24-hour in-memory cache.
Per-key asyncio locks prevent cache stampedes when multiple coroutines
request the same ticker simultaneously.
All I/O runs in a thread pool to keep the async event loop free.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from functools import partial
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 86_400  # 24 hours
_LOCKS: Dict[str, asyncio.Lock] = {}


def _cache_get(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


# ---------------------------------------------------------------------------
# Sync fetchers (run in thread pool)
# ---------------------------------------------------------------------------

def _fetch_info_sync(ticker: str) -> Dict:
    return yf.Ticker(ticker).info or {}


def _fetch_financials_sync(ticker: str) -> Dict:
    t = yf.Ticker(ticker)
    result: Dict[str, Any] = {}

    for attr, key in [
        ("balance_sheet", "balance_sheet"),
        ("income_stmt", "income_stmt"),
        ("cashflow", "cashflow"),
        ("quarterly_balance_sheet", "quarterly_balance_sheet"),
    ]:
        try:
            df = getattr(t, attr)
            result[key] = df if df is not None and not df.empty else None
        except Exception:
            result[key] = None

    return result


# ---------------------------------------------------------------------------
# Async public API — single fetch per ticker per TTL window (lock-protected)
# ---------------------------------------------------------------------------

async def get_info(ticker: str) -> Dict:
    key = f"info:{ticker}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    async with _get_lock(key):
        cached = _cache_get(key)  # re-check inside lock
        if cached is not None:
            return cached
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, partial(_fetch_info_sync, ticker))
            _cache_set(key, data)
            return data
        except Exception as exc:
            logger.warning("Failed to fetch info for %s: %s", ticker, exc)
            return {}


async def get_financials(ticker: str) -> Dict:
    key = f"financials:{ticker}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    async with _get_lock(key):
        cached = _cache_get(key)
        if cached is not None:
            return cached
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, partial(_fetch_financials_sync, ticker))
            _cache_set(key, data)
            return data
        except Exception as exc:
            logger.warning("Failed to fetch financials for %s: %s", ticker, exc)
            return {}


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------

def safe_get(df, row_labels: List[str], col_index: int = 0) -> Optional[float]:
    """Extract a scalar from a DataFrame, trying multiple row label variants."""
    if df is None or df.empty:
        return None
    for label in row_labels:
        if label in df.index:
            try:
                cols = df.columns
                if col_index >= len(cols):
                    return None
                val = df.loc[label, cols[col_index]]
                if val is None:
                    return None
                f = float(val)
                if math.isnan(f):
                    return None
                return f
            except Exception:
                continue
    return None


def safe_get_multi(df, row_labels: List[str], num_periods: int = 3) -> List[Optional[float]]:
    """Return up to num_periods values for a row (most recent first)."""
    if df is None or df.empty:
        return []
    for label in row_labels:
        if label in df.index:
            try:
                row = df.loc[label]
                values: List[Optional[float]] = []
                for i in range(min(num_periods, len(row))):
                    v = row.iloc[i]
                    try:
                        f = float(v)
                        values.append(None if math.isnan(f) else f)
                    except (TypeError, ValueError):
                        values.append(None)
                return values
            except Exception:
                continue
    return []


def invalidate_cache(ticker: str) -> None:
    keys_to_remove = [k for k in _CACHE if k.endswith(f":{ticker}")]
    for k in keys_to_remove:
        del _CACHE[k]

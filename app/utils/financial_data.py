"""
yfinance wrapper with 24-hour in-memory cache and retry logic.
All I/O is run in a thread pool to keep the async event loop free.
"""
from __future__ import annotations

import asyncio
import logging
import time
from functools import partial
from typing import Any, Dict, Optional, Tuple

import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Simple TTL cache: {cache_key: (timestamp, data)}
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 86_400  # 24 hours


def _cache_get(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)


def _fetch_ticker_sync(ticker: str) -> yf.Ticker:
    return yf.Ticker(ticker)


def _fetch_info_sync(ticker: str) -> Dict:
    t = yf.Ticker(ticker)
    return t.info or {}


def _fetch_financials_sync(ticker: str) -> Dict:
    t = yf.Ticker(ticker)
    result: Dict[str, Any] = {}

    try:
        bs = t.balance_sheet
        result["balance_sheet"] = bs if bs is not None and not bs.empty else None
    except Exception:
        result["balance_sheet"] = None

    try:
        inc = t.income_stmt
        result["income_stmt"] = inc if inc is not None and not inc.empty else None
    except Exception:
        result["income_stmt"] = None

    try:
        cf = t.cashflow
        result["cashflow"] = cf if cf is not None and not cf.empty else None
    except Exception:
        result["cashflow"] = None

    try:
        bs_q = t.quarterly_balance_sheet
        result["quarterly_balance_sheet"] = (
            bs_q if bs_q is not None and not bs_q.empty else None
        )
    except Exception:
        result["quarterly_balance_sheet"] = None

    return result


async def get_info(ticker: str) -> Dict:
    key = f"info:{ticker}"
    cached = _cache_get(key)
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

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, partial(_fetch_financials_sync, ticker))
        _cache_set(key, data)
        return data
    except Exception as exc:
        logger.warning("Failed to fetch financials for %s: %s", ticker, exc)
        return {}


def safe_get(df, row_labels: list, col_index: int = 0) -> Optional[float]:
    """
    Safely extract a value from a DataFrame by trying multiple row label variants.
    Returns None if not found or if the value is NaN.
    """
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
                import math
                if math.isnan(float(val)):
                    return None
                return float(val)
            except Exception:
                continue
    return None


def safe_get_multi(df, row_labels: list, num_periods: int = 3) -> list:
    """Return up to num_periods values for a given row (most recent first)."""
    if df is None or df.empty:
        return []
    for label in row_labels:
        if label in df.index:
            try:
                row = df.loc[label]
                values = []
                for i in range(min(num_periods, len(row))):
                    import math
                    v = row.iloc[i]
                    if v is not None and not math.isnan(float(v)):
                        values.append(float(v))
                    else:
                        values.append(None)
                return values
            except Exception:
                continue
    return []


def invalidate_cache(ticker: str) -> None:
    keys_to_remove = [k for k in _CACHE if k.endswith(f":{ticker}")]
    for k in keys_to_remove:
        del _CACHE[k]

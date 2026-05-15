"""Custom validators and guards for ticker input."""
from __future__ import annotations

import re


_TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z]{1,3})?$")


def validate_ticker(ticker: str) -> str:
    cleaned = ticker.strip().upper()
    if not _TICKER_PATTERN.match(cleaned):
        raise ValueError(f"Invalid ticker format: '{ticker}'")
    return cleaned


def validate_horizon_months(months: int) -> None:
    if months < 6:
        raise ValueError(
            "Investment horizon must be >= 6 months. "
            "This API does not support short-term/day-trading analysis."
        )

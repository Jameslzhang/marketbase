"""A-share trading calendar — real holiday-aware calendar, not just weekday checks.

Uses akshare to fetch the full SSE/SZSE trading calendar and caches it
locally.  Falls back to weekday-only checks if the cache is unavailable.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── cache ────────────────────────────────────────────────────────────

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "daily_runs" / "cache" / "trade_calendar.csv"

_calendar_dates: set[date] | None = None
_calendar_loaded: bool = False


def _load_calendar(path: Path | None = None) -> set[date]:
    """Load trading dates from local CSV cache, or fetch from akshare."""
    global _calendar_dates, _calendar_loaded

    if _calendar_loaded and _calendar_dates is not None:
        return _calendar_dates

    cache_path = path or _DEFAULT_CACHE_PATH

    # Try loading from cache
    if cache_path.is_file():
        try:
            import pandas as pd
            df = pd.read_csv(cache_path, dtype=str)
            dates = {
                date.fromisoformat(d) for d in df["trade_date"]
                if d and len(d) == 10
            }
            if dates:
                _calendar_dates = dates
                _calendar_loaded = True
                logger.debug("loaded %d trading dates from cache", len(dates))
                return dates
        except Exception as exc:
            logger.warning("failed to load trade calendar cache: %s", exc)

    # Try fetching from akshare
    try:
        import akshare
        df = akshare.tool_trade_date_hist_sina()
        dates = {date.fromisoformat(str(d)) for d in df["trade_date"]}
        if dates:
            _calendar_dates = dates
            _calendar_loaded = True
            # Save cache
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_path, index=False, encoding="utf-8")
            logger.info("fetched and cached %d trading dates from akshare", len(dates))
            return dates
    except Exception as exc:
        logger.warning("failed to fetch trade calendar from akshare: %s", exc)

    # Fallback: empty set, caller will use weekday heuristic
    _calendar_dates = set()
    _calendar_loaded = True
    return _calendar_dates


def _ensure_calendar(path: Path | None = None) -> set[date]:
    return _load_calendar(path)


# ── public API ────────────────────────────────────────────────────────

def is_trading_day(d: date | datetime, *, cache_path: Path | None = None) -> bool:
    """Return True if *d* is an A-share trading day.

    Uses the real SSE/SZSE holiday calendar when available; falls back to
    weekday-only check (Mon-Fri) if the calendar cache is empty.
    """
    if isinstance(d, datetime):
        d = d.date()
    calendar = _ensure_calendar(cache_path)
    if calendar:
        return d in calendar
    # Fallback: assume Mon-Fri are trading days
    return d.weekday() < 5


def latest_trading_day(
    before: date | datetime | None = None,
    *,
    cache_path: Path | None = None,
) -> date:
    """Return the latest trading day on or before *before* (default: today)."""
    if before is None:
        before = date.today()
    if isinstance(before, datetime):
        before = before.date()

    calendar = _ensure_calendar(cache_path)
    if calendar:
        sorted_dates = sorted(calendar, reverse=True)
        for d in sorted_dates:
            if d <= before:
                return d
        return before  # should not happen

    # Fallback: walk backwards skipping weekends
    d = before
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def is_cn_market_session(
    value: datetime,
    *,
    cache_path: Path | None = None,
) -> bool:
    """Return True if *value* falls within an A-share continuous auction session.

    Checks both the trading calendar (holidays) and the 9:15-15:30 time window.
    """
    if not is_trading_day(value, cache_path=cache_path):
        return False
    from datetime import time as clock_time
    local_time = value.astimezone(timezone(timedelta(hours=8))).timetz().replace(tzinfo=None)
    return clock_time(9, 15) <= local_time <= clock_time(15, 30)


def refresh_calendar(cache_path: Path | None = None) -> int:
    """Force refresh the trading calendar from akshare. Returns count of dates."""
    global _calendar_dates, _calendar_loaded
    _calendar_dates = None
    _calendar_loaded = False
    calendar = _ensure_calendar(cache_path)
    return len(calendar)
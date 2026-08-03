"""A 股交易日历 —— 基于真实节假日数据，非简单工作日判断。

通过 akshare 获取沪深交易所完整交易日历并本地缓存。
缓存不可用时降级为周一至周五工作日判断。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 缓存 ────────────────────────────────────────────────────────────

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "daily_runs" / "cache" / "trade_calendar.csv"

_calendar_dates: set[date] | None = None
_calendar_loaded: bool = False


def _load_calendar(path: Path | None = None) -> set[date]:
    """从本地 CSV 缓存加载交易日列表，缓存不存在时调用 akshare 拉取并保存."""
    global _calendar_dates, _calendar_loaded

    if _calendar_loaded and _calendar_dates is not None:
        return _calendar_dates

    cache_path = path or _DEFAULT_CACHE_PATH

    # 尝试从本地缓存加载
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

    # 尝试从 akshare 拉取
    try:
        import akshare
        df = akshare.tool_trade_date_hist_sina()
        dates = {date.fromisoformat(str(d)) for d in df["trade_date"]}
        if dates:
            _calendar_dates = dates
            _calendar_loaded = True
            # 保存到本地缓存
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_path, index=False, encoding="utf-8")
            logger.info("fetched and cached %d trading dates from akshare", len(dates))
            return dates
    except Exception as exc:
        logger.warning("failed to fetch trade calendar from akshare: %s", exc)

    # 降级：空集合，调用方使用工作日启发式判断
    _calendar_dates = set()
    _calendar_loaded = True
    return _calendar_dates


def _ensure_calendar(path: Path | None = None) -> set[date]:
    return _load_calendar(path)


# ── 公开 API ────────────────────────────────────────────────────────

def is_trading_day(d: date | datetime, *, cache_path: Path | None = None) -> bool:
    """判断 *d* 是否为 A 股交易日。

    优先使用真实节假日日历；缓存为空时降级为周一至周五判断。
    """
    if isinstance(d, datetime):
        d = d.date()
    calendar = _ensure_calendar(cache_path)
    if calendar:
        return d in calendar
    # 降级：假设周一至周五为交易日
    return d.weekday() < 5


def latest_trading_day(
    before: date | datetime | None = None,
    *,
    cache_path: Path | None = None,
) -> date:
    """返回 *before* 当天及之前最近的交易日（默认：今天）."""
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
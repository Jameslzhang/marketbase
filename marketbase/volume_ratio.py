"""Local volume_ratio computation from snapshot cumulative volume + daily history.

Does NOT download minute data — uses only the snapshot's cumulative volume and
the daily cache's last 5 trading-day volumes.  Minute data remains reserved for
Codex-specified stock VWAP / minute-chart requests.
"""

from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta, timezone
import json
from pathlib import Path

import pandas as pd


_CN_TRADING_MINUTES = 240  # 9:30-11:30 + 13:00-15:00
_CN_SESSION_START = clock_time(9, 30)
_CN_LUNCH_START = clock_time(11, 30)
_CN_LUNCH_END = clock_time(13, 0)
_CN_SESSION_END = clock_time(15, 0)
_VOLUME_RATIO_WINDOW = 5


def elapsed_trade_minutes(observed_at: datetime) -> int:
    """Return the number of completed trading minutes as of *observed_at*."""
    from marketbase.calendar import is_trading_day

    local = observed_at.astimezone(timezone(timedelta(hours=8)))
    if not is_trading_day(local):
        return _CN_TRADING_MINUTES  # non-trading day: use full day
    t = local.timetz().replace(tzinfo=None)
    if t < _CN_SESSION_START:
        return 0
    if t > _CN_SESSION_END:
        return _CN_TRADING_MINUTES
    morning_end = min(t, _CN_LUNCH_START)
    morning = max(0, (_datetime_minutes(morning_end) - _datetime_minutes(_CN_SESSION_START)))
    afternoon = 0
    if t >= _CN_LUNCH_END:
        afternoon = _datetime_minutes(t) - _datetime_minutes(_CN_LUNCH_END)
    return morning + afternoon


def _datetime_minutes(t: clock_time) -> int:
    return t.hour * 60 + t.minute


def compute_volume_ratios_batch(
    frame: pd.DataFrame,
    daily_cache_root: Path,
    observed_at: datetime,
) -> pd.DataFrame:
    """Add volume_ratio columns to a snapshot DataFrame in-place.

    The frame must have 'code' and 'volume' columns.
    Returns the same frame with added/mutated volume_ratio and metadata columns.
    """
    if frame.empty or "code" not in frame.columns or "volume" not in frame.columns:
        return frame

    result = frame.copy()
    elapsed = elapsed_trade_minutes(observed_at)
    method = "full_day_vs_prev_5d" if elapsed >= _CN_TRADING_MINUTES else "intraday_pace_vs_prev_5d"

    ratios: list[float | None] = []
    for _, row in result.iterrows():
        code = str(row["code"]).strip()
        vol = _safe_float(row.get("volume"))
        if vol is None or vol <= 0:
            ratios.append(None)
            continue
        avg_5d = _avg_5d_volume(code, daily_cache_root, observed_at=observed_at)
        if avg_5d is None or avg_5d <= 0:
            ratios.append(None)
            continue
        if elapsed >= _CN_TRADING_MINUTES:
            ratios.append(round(vol / avg_5d, 4))
        elif elapsed > 0:
            expected = avg_5d * (elapsed / _CN_TRADING_MINUTES)
            ratios.append(round(vol / expected, 4) if expected > 0 else None)
        else:
            ratios.append(None)

    result["volume_ratio"] = ratios
    result["volume_ratio_window"] = _VOLUME_RATIO_WINDOW
    result["volume_ratio_method"] = method
    result["elapsed_trade_minutes"] = elapsed
    return result


def _avg_5d_volume(
    code: str,
    cache_root: Path,
    *,
    observed_at: datetime | None = None,
) -> float | None:
    """Read the last 5 *completed* trading-day volumes from the daily cache.

    When *observed_at* is provided, rows on or after that date are excluded
    so intraday / incomplete data is never counted as a completed trading day.
    """
    cache_path = cache_root / f"{code}.json"
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return None
    cutoff_date = observed_at.date() if observed_at is not None else None
    # Detect whether the cached volume is in 手 (legacy Tencent) or 股 (current).
    volume_unit = payload.get("volume_unit", "")
    source = payload.get("source", "")
    needs_shou_conversion = (
        volume_unit != "shares"
        and (volume_unit == "shou" or source == "tencent")
    )
    volumes: list[float] = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        # Exclude rows on or after the observation date (incomplete intraday data)
        if cutoff_date is not None:
            row_date = row.get("date")
            if isinstance(row_date, str) and row_date >= cutoff_date.isoformat():
                continue
        vol = _safe_float(row.get("volume"))
        if vol is not None and vol > 0:
            if needs_shou_conversion:
                vol = vol * 100.0  # 手 → 股
            volumes.append(vol)
        if len(volumes) >= _VOLUME_RATIO_WINDOW:
            break
    if len(volumes) < _VOLUME_RATIO_WINDOW:
        return None
    return sum(volumes) / len(volumes)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None  # also checks NaN
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
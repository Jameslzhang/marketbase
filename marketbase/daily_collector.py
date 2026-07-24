"""支持断点续跑的全市场日线历史采集."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

import pandas as pd

from marketbase.daily import fetch_daily_history
from marketbase.indicators import compute_daily_indicators


def _atomic_replace(temp_path: Path, target_path: Path, *, retries: int = 5, delay: float = 0.1) -> None:
    """原子替换目标文件，Windows 锁冲突时重试."""
    for attempt in range(retries):
        try:
            temp_path.replace(target_path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


_SCHEMA_VERSION = 1
_DAILY_COLUMNS = ("date", "open", "high", "low", "close", "volume", "amount")
_MIN_INDICATOR_ROWS = 50  # 指标计算所需最少历史行数
_MARKET_CLOSE_HOUR = 15  # A 股收盘时间 15:00 北京时间


def _is_daily_cache_fresh(latest_date: str, observed_at: datetime) -> bool:
    """根据当前时间判断日线缓存是否足够新鲜。

    - 收盘后（北京时间 ≥15:00 且为交易日）：最新日期必须等于今天。
    - 交易时段中或开盘前：最新日期与今天相差不超过 1 天（昨天数据可用）。
    - 非交易日（周末）：最新日期与今天相差不超过 2 天（上周五数据可用）。
    """
    from datetime import timezone, timedelta

    try:
        latest = datetime.strptime(latest_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    today = observed_at.date()
    days_behind = (today - latest).days
    if days_behind < 0:
        return False  # future date in cache

    local = observed_at.astimezone(timezone(timedelta(hours=8)))
    after_close = local.hour >= _MARKET_CLOSE_HOUR

    from marketbase.calendar import is_trading_day

    if not is_trading_day(local):  # weekend or holiday
        return days_behind <= 2
    if after_close:
        return days_behind == 0
    return days_behind <= 1
_COLUMN_ALIASES = {
    "date": ("date", "日期", "交易日期", "trade_date", "day"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘"),
    "volume": ("volume", "vol", "成交量"),
    "amount": ("amount", "成交额"),
}


@dataclass(frozen=True)
class DailyProgressEvent:
    completed: int
    total: int
    success_count: int
    cache_hit_count: int
    failure_count: int
    current_code: str
    current_source: str
    wall_time: str
    elapsed_seconds: float
    rate_per_minute: float
    eta_seconds: float | None
    last_error: str


@dataclass(frozen=True)
class DailyCollectionReport:
    trading_date: str
    total_count: int
    success_count: int
    cache_hit_count: int
    failure_count: int
    pending_count: int
    source_counts: dict[str, int]
    errors: dict[str, str]
    started_at: str
    finished_at: str
    elapsed_seconds: float
    checkpoint_path: Path
    cache_root: Path
    indicators: list[dict[str, object]]
    latest_date_distribution: dict[str, int]
    short_history: list[dict[str, object]]
    invalid_or_missing_cache: list[dict[str, str]]
    indicator_insufficient: list[dict[str, object]]


@dataclass(frozen=True)
class _FetchResult:
    code: str
    source: str
    error: str
    indicators: dict[str, object] | None
    actual_rows: int
    latest_date: str


def collect_daily_universe(
    codes: Iterable[str],
    *,
    cache_root: str | Path,
    checkpoint_path: str | Path,
    lookback: int = 260,
    fetcher: Callable[..., pd.DataFrame] = fetch_daily_history,
    progress: Callable[[DailyProgressEvent], None] | None = None,
    now: datetime | None = None,
    max_workers: int = 15,
    incremental: bool = True,
) -> DailyCollectionReport:
    """Collect daily histories concurrently with incremental update.

    When *incremental* is True (default), existing caches are reused if their
    latest date is within 1 day of today; only the tail is fetched if the cache
    is stale by a few days.  A full refetch is only triggered when the cache is
    missing or more than 5 days stale.
    """
    normalized_codes = _validate_codes(codes)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 260:
        raise ValueError("lookback must be between 1 and 260")

    cache_dir = Path(cache_root)
    checkpoint = Path(checkpoint_path)
    observed_at = _coerce_now(now)
    trading_date = observed_at.date().isoformat()
    started_at = observed_at.isoformat()
    start_monotonic = time.monotonic()
    state = _load_checkpoint(
        checkpoint,
        trading_date=trading_date,
        lookback=lookback,
        codes=normalized_codes,
    )

    _lock = threading.Lock()
    completed_codes: list[str] = list(state["completed_codes"])
    failed_codes: list[str] = []  # always retry previously failed codes
    total = len(normalized_codes)
    # If the checkpoint shows all codes completed (previous run finished),
    # reset to empty so the incremental cache path can recompute indicators.
    if len(completed_codes) == total:
        completed_codes = []
        failed_codes = []
    success_count = 0
    cache_hit_count = 0
    failure_count = 0
    source_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    indicators_list: list[dict[str, object]] = []
    latest_date_distribution: dict[str, int] = {}
    short_history: list[dict[str, object]] = []
    invalid_or_missing: list[dict[str, str]] = []
    indicator_insufficient: list[dict[str, object]] = []

    def _emit_progress(current_code: str = "", current_source: str = "", last_error: str = "") -> None:
        """Emit a DailyProgressEvent to the optional progress callback."""
        completed = success_count + cache_hit_count + failure_count
        elapsed = max(time.monotonic() - start_monotonic, 0.0)
        pending = total - completed
        rate_per_minute = (completed * 60.0 / elapsed) if elapsed else 0.0
        eta_seconds = _eta_seconds(completed, pending, elapsed)
        if progress is not None:
            progress(
                DailyProgressEvent(
                    completed=completed,
                    total=total,
                    success_count=success_count,
                    cache_hit_count=cache_hit_count,
                    failure_count=failure_count,
                    current_code=current_code,
                    current_source=current_source,
                    wall_time=_current_time().isoformat(),
                    elapsed_seconds=elapsed,
                    rate_per_minute=rate_per_minute,
                    eta_seconds=eta_seconds,
                    last_error=last_error,
                )
            )

    def _write_checkpoint() -> None:
        """Persist the current collection state so interrupted runs can resume."""
        with _lock:
            cc = list(completed_codes)
            fc = list(failed_codes)
        try:
            _atomic_write_json(
                checkpoint,
                _checkpoint_payload(
                    trading_date=trading_date,
                    lookback=lookback,
                    codes=normalized_codes,
                    completed_codes=cc,
                    failed_codes=fc,
                    updated_at=_current_time(),
                ),
            )
        except Exception:  # noqa: BLE001 - checkpoint is non-critical
            pass

    def _fetch_one(code: str) -> _FetchResult:
        """Fetch and cache one code. Returns result with pre-computed indicators."""
        nonlocal cache_hit_count, success_count, failure_count
        nonlocal source_counts, errors, indicators_list
        nonlocal latest_date_distribution, short_history, invalid_or_missing, indicator_insufficient
        cache_path = cache_dir / f"{code}.json"
        observed_dt = _coerce_now(now)

        # --- incremental: try to reuse existing cache ---
        if incremental:
            existing = _read_existing_cache(cache_path, code)
            if existing is not None:
                existing_frame, existing_meta = existing
                latest_str = str(existing_frame["date"].iloc[-1])
                try:
                    latest_date = datetime.strptime(latest_str, "%Y-%m-%d").date()
                    days_behind = (observed_dt.date() - latest_date).days
                except ValueError:
                    days_behind = 999

                if _is_daily_cache_fresh(latest_str, observed_dt):
                    requested_lb = int(existing_meta.get("requested_lookback", 0))
                    if requested_lb >= lookback:
                        # cache is fresh AND has enough lookback — reuse without any network call
                        indicators = _compute_indicators_safe(existing_frame, observed_dt)
                        actual_rows = len(existing_frame)
                        with _lock:
                            cache_hit_count += 1
                            cached_src = str(existing_meta["source"])
                            source_counts[cached_src] = source_counts.get(cached_src, 0) + 1
                            _mark_completed(completed_codes, failed_codes, code)
                            indicators_list.append({"code": code, **indicators})
                            latest_date_distribution[latest_str] = latest_date_distribution.get(latest_str, 0) + 1
                            if actual_rows < lookback:
                                short_history.append({"code": code, "actual_rows": actual_rows, "reason": "short_history"})
                            _track_indicator_quality(code, actual_rows, indicators, indicator_insufficient)
                        return _FetchResult(
                            code=code, source=cached_src, error="",
                            indicators=indicators,
                            actual_rows=actual_rows,
                            latest_date=latest_str,
                        )
                    # cache date is fresh but lookback insufficient — fall through to full fetch for backfill
                elif days_behind <= 5:
                    # slightly stale — fetch tail only, then merge
                    try:
                        tail = fetcher(code, lookback_days=days_behind + 3, source="auto", retries=2)
                        tail_frame = _normalize_history(tail, lookback)
                        merged = _merge_frames(existing_frame, tail_frame, lookback)
                        actual_rows = len(merged)
                        latest_str = str(merged["date"].iloc[-1])
                        actual_source = str(tail.attrs.get("daily_source") or "").strip()
                        if not actual_source:
                            raise ValueError("daily source is missing")
                        source_errors = _normalize_source_errors(tail.attrs.get("source_errors"))
                        _write_cache_entry(cache_path, code, merged, trading_date, lookback, actual_source, source_errors)
                        indicators = _compute_indicators_safe(merged, observed_dt)
                        with _lock:
                            success_count += 1
                            source_counts[actual_source] = source_counts.get(actual_source, 0) + 1
                            _mark_completed(completed_codes, failed_codes, code)
                            indicators_list.append({"code": code, **indicators})
                            latest_date_distribution[latest_str] = latest_date_distribution.get(latest_str, 0) + 1
                            if actual_rows < lookback:
                                short_history.append({"code": code, "actual_rows": actual_rows, "reason": "short_history"})
                            _track_indicator_quality(code, actual_rows, indicators, indicator_insufficient)
                        return _FetchResult(
                            code=code, source=actual_source, error="",
                            indicators=indicators,
                            actual_rows=actual_rows,
                            latest_date=latest_str,
                        )
                    except Exception:
                        # tail fetch failed — fall through to full fetch
                        pass

        # --- full fetch (fallback) ---
        try:
            history = fetcher(code, lookback_days=lookback, source="auto", retries=2)
            frame = _normalize_history(history, lookback)
            actual_rows = len(frame)
            latest_str = str(frame["date"].iloc[-1])
            actual_source = str(history.attrs.get("daily_source") or "").strip()
            if not actual_source:
                raise ValueError("daily source is missing")
            source_errors = _normalize_source_errors(history.attrs.get("source_errors"))
            _write_cache_entry(cache_path, code, frame, trading_date, lookback, actual_source, source_errors)
            indicators = _compute_indicators_safe(frame, observed_dt)
            with _lock:
                success_count += 1
                source_counts[actual_source] = source_counts.get(actual_source, 0) + 1
                _mark_completed(completed_codes, failed_codes, code)
                indicators_list.append({"code": code, **indicators})
                latest_date_distribution[latest_str] = latest_date_distribution.get(latest_str, 0) + 1
                if actual_rows < lookback:
                    short_history.append({"code": code, "actual_rows": actual_rows, "reason": "short_history"})
                _track_indicator_quality(code, actual_rows, indicators, indicator_insufficient)
            return _FetchResult(
                code=code, source=actual_source, error="",
                indicators=indicators,
                actual_rows=actual_rows,
                latest_date=latest_str,
            )
        except Exception as exc:  # noqa: BLE001 - one code must not stop the batch
            error = _neutral_error(str(exc) or type(exc).__name__)
            with _lock:
                failure_count += 1
                errors[code] = error
                _mark_failed(completed_codes, failed_codes, code)
                invalid_or_missing.append({"code": code, "reason": "fetch_error"})
            return _FetchResult(
                code=code, source="", error=error,
                indicators=None, actual_rows=0, latest_date="",
            )

    _emit_progress()
    completed_after_last_checkpoint = 0
    completed_after_last_log = 0

    done_set = set(completed_codes)  # only skip successfully completed codes; retry failures

    # --- audit checkpoint-skipped codes so short_history, indicators, etc. stay accurate ---
    def _audit_skipped(code: str) -> None:
        """Re-audit a checkpoint-skipped code from its cache."""
        nonlocal cache_hit_count
        cache_path = cache_dir / f"{code}.json"
        existing = _read_existing_cache(cache_path, code)
        if existing is None:
            with _lock:
                invalid_or_missing.append({"code": code, "reason": "checkpoint_cache_missing"})
            return
        existing_frame, existing_meta = existing
        latest_str = str(existing_frame["date"].iloc[-1])
        actual_rows = len(existing_frame)
        indicators = _compute_indicators_safe(existing_frame, observed_at)
        cached_src = str(existing_meta["source"])
        with _lock:
            cache_hit_count += 1
            source_counts[cached_src] = source_counts.get(cached_src, 0) + 1
            indicators_list.append({"code": code, **indicators})
            latest_date_distribution[latest_str] = latest_date_distribution.get(latest_str, 0) + 1
            if actual_rows < lookback:
                short_history.append({"code": code, "actual_rows": actual_rows, "reason": "short_history"})
            _track_indicator_quality(code, actual_rows, indicators, indicator_insufficient)

    for code in normalized_codes:
        if code in done_set:
            _audit_skipped(code)

    pending_codes = [c for c in normalized_codes if c not in done_set]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in pending_codes}
        for future in as_completed(futures):
            result = future.result()
            with _lock:
                completed_after_last_checkpoint += 1
                completed_after_last_log += 1

            if completed_after_last_checkpoint >= 100:
                _write_checkpoint()
                completed_after_last_checkpoint = 0

            if completed_after_last_log >= 50:
                _emit_progress(
                    current_code=result.code,
                    current_source=result.source,
                    last_error=result.error,
                )
                completed_after_last_log = 0

    _write_checkpoint()
    _emit_progress()

    elapsed = max(time.monotonic() - start_monotonic, 0.0)
    return DailyCollectionReport(
        trading_date=trading_date,
        total_count=total,
        success_count=success_count,
        cache_hit_count=cache_hit_count,
        failure_count=failure_count,
        pending_count=0,
        source_counts=source_counts,
        errors=errors,
        started_at=started_at,
        finished_at=_current_time().isoformat(),
        elapsed_seconds=elapsed,
        checkpoint_path=checkpoint,
        cache_root=cache_dir,
        indicators=indicators_list,
        latest_date_distribution=latest_date_distribution,
        short_history=short_history,
        invalid_or_missing_cache=invalid_or_missing,
        indicator_insufficient=indicator_insufficient,
    )


def read_daily_cache(path: str | Path) -> tuple[pd.DataFrame, dict[str, object]]:
    """Read and validate a collector cache entry."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("daily cache cannot be read") from exc
    if not isinstance(payload, dict):
        raise ValueError("daily cache must be a JSON object")
    required = {
        "schema_version",
        "code",
        "fetched_at",
        "trading_date",
        "requested_lookback",
        "actual_rows",
        "latest_date",
        "source",
        "source_errors",
        "volume_unit",
        "rows",
    }
    if set(payload) != required or not _is_schema_version(payload["schema_version"]):
        raise ValueError("daily cache schema is invalid")
    if not isinstance(payload["rows"], list) or not payload["rows"] or len(payload["rows"]) > 260:
        raise ValueError("daily cache has no rows")
    if not all(isinstance(row, dict) and set(row) == set(_DAILY_COLUMNS) for row in payload["rows"]):
        raise ValueError("daily cache row shape is invalid")
    frame = pd.DataFrame(payload["rows"], columns=_DAILY_COLUMNS)
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():
        raise ValueError("daily cache dates are invalid")
    normalized = _normalize_history(frame, 260)
    if len(normalized) != len(frame):
        raise ValueError("daily cache rows are invalid")
    frame = normalized
    metadata = {key: value for key, value in payload.items() if key != "rows"}
    if not _metadata_matches_frame(metadata, frame):
        raise ValueError("daily cache metadata is invalid")
    return frame, metadata


def _validate_codes(codes: Iterable[str]) -> list[str]:
    normalized = list(codes)
    if any(not isinstance(code, str) or len(code) != 6 or not code.isdigit() for code in normalized):
        raise ValueError("codes must be unique six-digit strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("codes must be unique six-digit strings")
    return normalized


def _coerce_now(now: datetime | None) -> datetime:
    observed = now or _current_time()
    return observed.astimezone() if observed.tzinfo is None else observed


def _current_time() -> datetime:
    return datetime.now().astimezone()


def _normalize_history(history: pd.DataFrame, lookback: int) -> pd.DataFrame:
    if not isinstance(history, pd.DataFrame) or history.empty:
        raise ValueError("daily history is empty")
    selected: dict[str, pd.Series] = {}
    for target, aliases in _COLUMN_ALIASES.items():
        column = next((name for name in aliases if name in history.columns), None)
        if column is not None:
            selected[target] = history[column]
    if "date" not in selected or "close" not in selected:
        raise ValueError("daily history lacks date or close")
    normalized = pd.DataFrame(selected)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.dropna(subset=["date", "close"])
    if normalized.empty:
        raise ValueError("daily history has no usable close data")
    for column in ("open", "high", "low", "volume", "amount"):
        normalized[column] = pd.to_numeric(normalized.get(column), errors="coerce")
    normalized = normalized.drop_duplicates(subset=["date"], keep="last")
    normalized = normalized.sort_values("date").tail(lookback).reset_index(drop=True)
    normalized["date"] = normalized["date"].dt.date.astype(str)
    return normalized.loc[:, _DAILY_COLUMNS]


def _cache_payload(
    *,
    code: str,
    frame: pd.DataFrame,
    trading_date: str,
    lookback: int,
    fetched_at: datetime,
    source: str,
    source_errors: list[str],
) -> dict[str, object]:
    rows = frame.to_dict(orient="records")
    return {
        "schema_version": _SCHEMA_VERSION,
        "code": code,
        "fetched_at": fetched_at.isoformat(),
        "trading_date": trading_date,
        "requested_lookback": lookback,
        "actual_rows": len(frame),
        "latest_date": str(frame["date"].iloc[-1]),
        "source": source,
        "source_errors": source_errors,
        "volume_unit": "shares",
        "rows": rows,
    }


def _write_cache_entry(
    cache_path: Path,
    code: str,
    frame: pd.DataFrame,
    trading_date: str,
    lookback: int,
    source: str,
    source_errors: list[str],
) -> None:
    """Write a cache entry, reusing _cache_payload + _atomic_write_json."""
    payload = _cache_payload(
        code=code,
        frame=frame,
        trading_date=trading_date,
        lookback=lookback,
        fetched_at=_current_time(),
        source=source,
        source_errors=source_errors,
    )
    _atomic_write_json(cache_path, payload)


def _read_existing_cache(
    path: Path, code: str
) -> tuple[pd.DataFrame, dict[str, object]] | None:
    """Read an existing cache entry without strict trading_date/lookback checks.

    Used by incremental mode to decide whether the cache is fresh enough.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if not _is_schema_version(payload.get("schema_version")):
        return None
    if payload.get("code") != code:
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(row, dict) and set(row) == set(_DAILY_COLUMNS) for row in rows):
        return None
    frame = pd.DataFrame(rows, columns=list(_DAILY_COLUMNS))
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():
        return None
    if len(frame) > 260:
        return None
    metadata = {key: value for key, value in payload.items() if key != "rows"}
    return frame, metadata


def _compute_indicators_safe(
    frame: pd.DataFrame, observed_at: datetime
) -> dict[str, object]:
    """Compute indicators, returning empty dict on failure."""
    try:
        trading_date = observed_at.date().isoformat() if observed_at else None
        return compute_daily_indicators(frame, calculated_at=observed_at, trading_date=trading_date)
    except Exception:
        return {}


def _track_indicator_quality(
    code: str,
    actual_rows: int,
    indicators: dict[str, object],
    indicator_insufficient: list[dict[str, object]],
) -> None:
    """Track codes with insufficient history for meaningful indicator computation."""
    if actual_rows < _MIN_INDICATOR_ROWS:
        indicator_insufficient.append({
            "code": code,
            "actual_rows": actual_rows,
            "min_required": _MIN_INDICATOR_ROWS,
            "ma5": indicators.get("ma5"),
            "ma10": indicators.get("ma10"),
        })


def _merge_frames(
    existing: pd.DataFrame, tail: pd.DataFrame, lookback: int
) -> pd.DataFrame:
    """Merge existing cache with newly fetched tail, deduplicate, keep last *lookback* rows."""
    combined = pd.concat([existing, tail], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    combined = combined.sort_values("date").tail(lookback).reset_index(drop=True)
    combined["date"] = combined["date"].astype(str)
    return combined.loc[:, list(_DAILY_COLUMNS)]


def _metadata_matches_frame(metadata: dict[str, object], frame: pd.DataFrame) -> bool:
    return (
        isinstance(metadata["code"], str)
        and len(metadata["code"]) == 6
        and metadata["code"].isdigit()
        and _is_timezone_aware_iso(metadata["fetched_at"])
        and isinstance(metadata["trading_date"], str)
        and _is_non_bool_int(metadata["requested_lookback"])
        and 1 <= metadata["requested_lookback"] <= 260
        and _is_non_bool_int(metadata["actual_rows"])
        and metadata["actual_rows"] == len(frame)
        and 0 < len(frame) <= 260
        and metadata["latest_date"] == str(frame["date"].iloc[-1])
        and isinstance(metadata["source"], str)
        and bool(metadata["source"].strip())
        and isinstance(metadata["source_errors"], list)
        and all(isinstance(item, str) for item in metadata["source_errors"])
    )


def _normalize_source_errors(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _neutral_error(value: str) -> str:
    result = value
    for term in ("候选", "推荐", "买入", "卖出", "概率"):
        result = result.replace(term, "数据")
    return result


def _load_checkpoint(path: Path, *, trading_date: str, lookback: int, codes: list[str]) -> dict[str, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed_codes": [], "failed_codes": []}
    if not isinstance(payload, dict):
        return {"completed_codes": [], "failed_codes": []}
    if (
        set(payload)
        != {
            "schema_version",
            "trading_date",
            "requested_lookback",
            "total_codes",
            "completed_codes",
            "failed_codes",
            "updated_at",
        }
        or not _is_schema_version(payload.get("schema_version"))
        or payload.get("trading_date") != trading_date
        or not _is_non_bool_int(payload.get("requested_lookback"))
        or payload["requested_lookback"] != lookback
        or payload.get("total_codes") != codes
        or not _valid_checkpoint_codes(payload.get("completed_codes"), codes)
        or not _valid_checkpoint_codes(payload.get("failed_codes"), codes)
        or set(payload["completed_codes"]) & set(payload["failed_codes"])
        or not _is_timezone_aware_iso(payload.get("updated_at"))
    ):
        return {"completed_codes": [], "failed_codes": []}
    return {
        "completed_codes": list(payload["completed_codes"]),
        "failed_codes": list(payload["failed_codes"]),
    }


def _is_schema_version(value: object) -> bool:
    return _is_non_bool_int(value) and value == _SCHEMA_VERSION


def _is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_timezone_aware_iso(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _valid_checkpoint_codes(value: object, codes: list[str]) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(code, str) and code in codes for code in value)
        and len(value) == len(set(value))
    )


def _checkpoint_payload(
    *,
    trading_date: str,
    lookback: int,
    codes: list[str],
    completed_codes: list[str],
    failed_codes: list[str],
    updated_at: datetime,
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "trading_date": trading_date,
        "requested_lookback": lookback,
        "total_codes": codes,
        "completed_codes": completed_codes,
        "failed_codes": failed_codes,
        "updated_at": updated_at.isoformat(),
    }


def _mark_completed(completed_codes: list[str], failed_codes: list[str], code: str) -> None:
    if code not in completed_codes:
        completed_codes.append(code)
    if code in failed_codes:
        failed_codes.remove(code)


def _mark_failed(completed_codes: list[str], failed_codes: list[str], code: str) -> None:
    if code in completed_codes:
        completed_codes.remove(code)
    if code not in failed_codes:
        failed_codes.append(code)


def _eta_seconds(completed: int, pending: int, elapsed_seconds: float) -> float | None:
    if pending == 0:
        return 0.0
    if completed == 0 or elapsed_seconds == 0:
        return None
    return pending / (completed / elapsed_seconds)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

"""Resumable full-universe daily history collection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

import pandas as pd

from alphasift.daily import fetch_daily_history


_SCHEMA_VERSION = 1
_DAILY_COLUMNS = ("date", "open", "high", "low", "close", "volume", "amount")
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


def collect_daily_universe(
    codes: Iterable[str],
    *,
    cache_root: str | Path,
    checkpoint_path: str | Path,
    lookback: int = 250,
    fetcher: Callable[..., pd.DataFrame] = fetch_daily_history,
    progress: Callable[[DailyProgressEvent], None] | None = None,
    now: datetime | None = None,
) -> DailyCollectionReport:
    """Collect daily histories in order, using same-day valid cache entries."""
    normalized_codes = _validate_codes(codes)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 250:
        raise ValueError("lookback must be between 1 and 250")

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
    completed_codes = list(state["completed_codes"])
    failed_codes = list(state["failed_codes"])
    success_count = 0
    cache_hit_count = 0
    failure_count = 0
    source_counts: dict[str, int] = {}
    errors: dict[str, str] = {}

    for code in normalized_codes:
        error = ""
        source = ""
        cache_path = cache_dir / f"{code}.json"
        metadata = _valid_cache_metadata(cache_path, code, trading_date, lookback)
        if metadata is not None:
            cache_hit_count += 1
            source = "cache"
            cached_source = str(metadata["source"])
            source_counts[cached_source] = source_counts.get(cached_source, 0) + 1
            _mark_completed(completed_codes, failed_codes, code)
        else:
            try:
                history = fetcher(code, lookback_days=lookback, source="auto", retries=2)
                frame = _normalize_history(history, lookback)
                actual_source = str(history.attrs.get("daily_source") or "").strip()
                if not actual_source:
                    raise ValueError("daily source is missing")
                source_errors = _normalize_source_errors(history.attrs.get("source_errors"))
                cache_payload = _cache_payload(
                    code=code,
                    frame=frame,
                    trading_date=trading_date,
                    lookback=lookback,
                    fetched_at=_current_time(),
                    source=actual_source,
                    source_errors=source_errors,
                )
                _atomic_write_json(cache_path, cache_payload)
                success_count += 1
                source = actual_source
                source_counts[actual_source] = source_counts.get(actual_source, 0) + 1
                _mark_completed(completed_codes, failed_codes, code)
            except Exception as exc:  # noqa: BLE001 - one code must not stop the batch
                failure_count += 1
                error = _neutral_error(str(exc) or type(exc).__name__)
                errors[code] = error
                _mark_failed(completed_codes, failed_codes, code)

        _atomic_write_json(
            checkpoint,
            _checkpoint_payload(
                trading_date=trading_date,
                lookback=lookback,
                codes=normalized_codes,
                completed_codes=completed_codes,
                failed_codes=failed_codes,
                updated_at=_current_time(),
            ),
        )
        completed = success_count + cache_hit_count + failure_count
        elapsed = max(time.monotonic() - start_monotonic, 0.0)
        pending = len(normalized_codes) - completed
        rate_per_minute = (completed * 60.0 / elapsed) if elapsed else 0.0
        eta_seconds = _eta_seconds(completed, pending, elapsed)
        if progress is not None:
            progress(
                DailyProgressEvent(
                    completed=completed,
                    total=len(normalized_codes),
                    success_count=success_count,
                    cache_hit_count=cache_hit_count,
                    failure_count=failure_count,
                    current_code=code,
                    current_source=source,
                    wall_time=_current_time().isoformat(),
                    elapsed_seconds=elapsed,
                    rate_per_minute=rate_per_minute,
                    eta_seconds=eta_seconds,
                    last_error=error,
                )
            )

    elapsed = max(time.monotonic() - start_monotonic, 0.0)
    return DailyCollectionReport(
        trading_date=trading_date,
        total_count=len(normalized_codes),
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
        "rows",
    }
    if set(payload) != required or not _is_schema_version(payload["schema_version"]):
        raise ValueError("daily cache schema is invalid")
    if not isinstance(payload["rows"], list) or not payload["rows"] or len(payload["rows"]) > 250:
        raise ValueError("daily cache has no rows")
    if not all(isinstance(row, dict) and set(row) == set(_DAILY_COLUMNS) for row in payload["rows"]):
        raise ValueError("daily cache row shape is invalid")
    frame = pd.DataFrame(payload["rows"], columns=_DAILY_COLUMNS)
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():
        raise ValueError("daily cache dates are invalid")
    normalized = _normalize_history(frame, 250)
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
    rows = json.loads(frame.to_json(orient="records"))
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
        "rows": rows,
    }


def _valid_cache_metadata(path: Path, code: str, trading_date: str, lookback: int) -> dict[str, object] | None:
    try:
        _, metadata = read_daily_cache(path)
    except ValueError:
        return None
    if (
        metadata["code"] != code
        or metadata["trading_date"] != trading_date
        or metadata["requested_lookback"] != lookback
    ):
        return None
    return metadata


def _metadata_matches_frame(metadata: dict[str, object], frame: pd.DataFrame) -> bool:
    return (
        isinstance(metadata["code"], str)
        and len(metadata["code"]) == 6
        and metadata["code"].isdigit()
        and _is_timezone_aware_iso(metadata["fetched_at"])
        and isinstance(metadata["trading_date"], str)
        and _is_non_bool_int(metadata["requested_lookback"])
        and 1 <= metadata["requested_lookback"] <= 250
        and _is_non_bool_int(metadata["actual_rows"])
        and metadata["actual_rows"] == len(frame)
        and 0 < len(frame) <= 250
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
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

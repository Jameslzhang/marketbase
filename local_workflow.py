"""Objective local collection entry point and request fulfillment command."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised through the POSIX lock branch.
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised through the Windows lock branch.
    fcntl = None

import pandas as pd

from alphasift.classification_map import build_classification_map
from alphasift.daily import fetch_daily_history
from alphasift.daily_collector import (
    DailyCollectionReport,
    DailyProgressEvent,
    collect_daily_universe,
    read_daily_cache,
)
from alphasift.data_request import load_data_request, write_data_response
from alphasift.market_collector import MarketCollectionResult, collect_market_snapshot
from alphasift.minute_collector import collect_requested_data
from alphasift.neutral_indicators import compute_daily_indicators


_INDICATOR_FIELDS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ma250",
    "rsi14",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "atr14",
    "atr14_pct",
    "input_rows",
    "first_date",
    "last_date",
    "calculated_at",
)
_LATEST_THREAD_LOCK = threading.Lock()
_FORBIDDEN_TERMS = (
    "candidate",
    "recommend",
    "buy",
    "sell",
    "probability",
    "rank",
    "score",
    "mainline",
    "tier",
    "候选",
    "推荐",
    "买入",
    "卖出",
    "概率",
    "排名",
    "评分",
    "主线",
    "梯队",
)


def run_collection(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    progress: Callable[[str], None] = print,
    providers: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Collect current market facts and associated daily/cache evidence."""
    root = Path(data_root).expanduser().resolve()
    observed_at = _observed_at(now)
    configured = dict(providers or {})
    run_dir = _create_run_directory(root, observed_at)
    log_path = run_dir / "workflow.log"

    def emit(message: str) -> None:
        text = f"{observed_at.isoformat()} {_neutral_text(message)}"
        progress(text)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")

    emit("collection_started")
    cache_root = root / "cache"
    market_cache_path = cache_root / "market_snapshot.json"
    market_collector = configured.get("market_collector", collect_market_snapshot)
    result = _call_market_collector(
        market_collector,
        cache_path=market_cache_path,
        now=observed_at,
        progress=emit,
    )
    frame = result.frame.copy()
    _ensure_market_cache(market_cache_path, observed_at, result, frame)
    codes = _unique_codes(frame)
    emit(f"market_rows={len(frame)}")

    daily_fetcher = configured.get("daily_fetcher", fetch_daily_history)
    daily_report = collect_daily_universe(
        codes,
        cache_root=cache_root / "daily",
        checkpoint_path=cache_root / "daily_checkpoint.json",
        lookback=250,
        fetcher=daily_fetcher,
        progress=lambda event: emit(_daily_progress_message(event)),
        now=observed_at,
    )
    indicators, indicator_errors = _collect_indicators(
        codes,
        cache_root / "daily",
        observed_at,
    )
    existing_map = _existing_map(root, configured.get("existing_map"))
    supply_chain_path = _supply_chain_path(root, configured.get("supply_chain_path"))
    classification, classification_audit = build_classification_map(
        frame,
        existing_map=existing_map,
        supply_chain_path=supply_chain_path,
    )

    _write_csv_atomic(run_dir / "market_snapshot.csv", frame)
    _write_json_atomic(
        run_dir / "market_snapshot.json",
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "rows": _frame_records(frame),
        },
    )
    _write_csv_atomic(run_dir / "daily_indicators.csv", indicators)
    _write_csv_atomic(run_dir / "classification_map.csv", classification)
    _write_csv_atomic(root / "classification_map.csv", classification)

    daily_audit = _daily_audit(daily_report, codes=codes, cache_root=cache_root / "daily")
    audit = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "market": _json_value(result.audit),
        "daily": daily_audit,
        "indicators": {
            "success_count": len(indicators),
            "failure_count": len(indicator_errors),
            "errors": indicator_errors,
        },
        "classification": _json_value(classification_audit),
        "provider_errors": _provider_errors(result, daily_report),
    }
    _write_json_atomic(run_dir / "data_audit.json", audit)
    emit("collection_completed")

    files = _file_records(
        run_dir,
        {
            "market_snapshot_csv": len(frame),
            "market_snapshot_json": len(frame),
            "daily_indicators": len(indicators),
            "classification_map": len(classification),
            "data_audit": 1,
            "workflow_log": 0,
        },
    )
    manifest = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "run_dir": str(run_dir),
        "files": files,
        "cache_paths": _cache_paths(root),
        "errors": _error_summary(audit),
    }
    _write_json_atomic(run_dir / "manifest.json", manifest)
    latest_path = root / "latest_codex_input.json"
    _publish_latest(
        latest_path,
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "run_dir": str(run_dir),
            "market_snapshot_path": str((run_dir / "market_snapshot.json").resolve()),
            "daily_indicators_path": str((run_dir / "daily_indicators.csv").resolve()),
            "classification_map_path": str((run_dir / "classification_map.csv").resolve()),
            "data_audit_path": str((run_dir / "data_audit.json").resolve()),
            "manifest_path": str((run_dir / "manifest.json").resolve()),
            "cache_paths": _cache_paths(root),
            "audit": audit,
        },
    )

    return {
        "run_dir": str(run_dir),
        "generated_at": observed_at.isoformat(),
        "market_rows": len(frame),
        "daily_success": daily_report.success_count + daily_report.cache_hit_count,
        "daily_failure": daily_report.failure_count,
        "indicator_rows": len(indicators),
        "classification_rows": len(classification),
        "latest_input_path": str(latest_path),
        "files": files,
    }


def fulfill_request(
    *,
    request_path: str | Path,
    response_path: str | Path,
    data_root: str | Path,
    now: datetime | None = None,
    providers: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Fulfill one validated request using only the daily cache and request scopes."""
    observed_at = _observed_at(now)
    request = load_data_request(request_path, today=observed_at.date())
    configured = dict(providers or {})
    collection_args: dict[str, object] = {
        "daily_cache_root": Path(data_root).expanduser().resolve() / "cache" / "daily",
        "daily_fetcher": configured.get("daily_fetcher", fetch_daily_history),
        "now": observed_at,
    }
    if "minute_fetcher" in configured:
        collection_args["minute_fetcher"] = configured["minute_fetcher"]
    payload = collect_requested_data(request, **collection_args)
    write_data_response(response_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Run collection by default or fulfill a strictly validated data request."""
    parser = argparse.ArgumentParser(description="AlphaSift 客观数据入口")
    parser.add_argument("--data-root", type=Path)
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("collect")
    request_parser = subcommands.add_parser("fulfill-request")
    request_parser.add_argument("--request", type=Path)
    request_parser.add_argument("--response", type=Path)
    arguments = parser.parse_args(argv)
    default_root = Path(__file__).resolve().parent / "data" / "daily_runs"

    try:
        if arguments.command == "fulfill-request":
            root = arguments.data_root or default_root
            fulfill_request(
                request_path=arguments.request or root / "codex_data_request.json",
                response_path=arguments.response or root / "codex_data_response.json",
                data_root=root,
            )
            print("客观数据请求补数完成")
            return 0
        summary = run_collection(
            data_root=getattr(arguments, "data_root", None) or default_root
        )
        print(
            "客观数据采集完成: "
            f"市场行数={summary['market_rows']} "
            f"日线成功={summary['daily_success']} 日线失败={summary['daily_failure']}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - command boundary prints one neutral failure.
        print(f"数据采集错误: {_neutral_text(str(exc) or type(exc).__name__)}")
        return 1


def _call_market_collector(
    collector: object,
    *,
    cache_path: Path,
    now: datetime,
    progress: Callable[[str], None],
) -> MarketCollectionResult:
    if not callable(collector):
        raise TypeError("market_collector must be callable")
    result = collector(cache_path=cache_path, now=now, progress=progress)
    if not isinstance(getattr(result, "frame", None), pd.DataFrame):
        raise TypeError("market_collector returned no market frame")
    return result


def _collect_indicators(
    codes: Sequence[str], cache_root: Path, observed_at: datetime
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for code in codes:
        try:
            frame, _ = read_daily_cache(cache_root / f"{code}.json")
            indicators = compute_daily_indicators(frame, calculated_at=observed_at)
            rows.append({"code": code, **{field: indicators[field] for field in _INDICATOR_FIELDS}})
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"code": code, "error": _neutral_text(str(exc) or type(exc).__name__)})
    return pd.DataFrame(rows, columns=("code", *_INDICATOR_FIELDS)), errors


def _ensure_market_cache(
    path: Path,
    observed_at: datetime,
    result: MarketCollectionResult,
    frame: pd.DataFrame,
) -> None:
    if path.is_file():
        return
    _write_json_atomic(
        path,
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "report": _json_value(getattr(result, "report", {})),
            "audit": _json_value(getattr(result, "audit", {})),
            "rows": _frame_records(frame),
        },
    )


def _existing_map(root: Path, configured: object) -> pd.DataFrame | None:
    if configured is not None:
        return configured if isinstance(configured, pd.DataFrame) else None
    path = root / "classification_map.csv"
    if not path.is_file():
        return None
    try:
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError):
        return None


def _supply_chain_path(root: Path, configured: object) -> str | Path | None:
    if configured is not None:
        return configured if isinstance(configured, (str, Path)) else None
    path = root / "supply_chain_map.csv"
    return path if path.is_file() else None


def _daily_progress_message(event: DailyProgressEvent) -> str:
    eta = "unknown" if event.eta_seconds is None else f"{event.eta_seconds:.2f}s"
    pending = max(event.total - event.completed, 0)
    return (
        f"daily_completed={event.completed}/{event.total} "
        f"wall_time={event.wall_time} elapsed={event.elapsed_seconds:.2f}s "
        f"rate={event.rate_per_minute:.2f}/min eta={eta} completed={event.completed} "
        f"cache_hits={event.cache_hit_count} failures={event.failure_count} pending={pending} "
        f"current_code={event.current_code} current_source={event.current_source} "
        f"last_error={_neutral_text(event.last_error)}"
    )


def _daily_audit(
    report: DailyCollectionReport, *, codes: Sequence[str], cache_root: Path
) -> dict[str, object]:
    payload = asdict(report)
    payload["checkpoint_path"] = str(report.checkpoint_path.resolve())
    payload["cache_root"] = str(report.cache_root.resolve())
    payload["errors"] = {code: _neutral_text(error) for code, error in report.errors.items()}
    payload.update(_scan_daily_cache(codes, cache_root, report.trading_date, 250))
    return _json_value(payload)


def _scan_daily_cache(
    codes: Sequence[str], cache_root: Path, trading_date: str, requested_lookback: int
) -> dict[str, object]:
    coverage_count = 0
    short_history: list[dict[str, object]] = []
    invalid_or_missing: list[dict[str, str]] = []
    latest_dates: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for code in codes:
        path = cache_root / f"{code}.json"
        try:
            _, metadata = read_daily_cache(path)
        except ValueError:
            invalid_or_missing.append(
                {"code": code, "reason": "missing_cache" if not path.is_file() else "invalid_cache"}
            )
            continue
        if (
            metadata["code"] != code
            or metadata["trading_date"] != trading_date
            or metadata["requested_lookback"] != requested_lookback
        ):
            invalid_or_missing.append({"code": code, "reason": "invalid_cache"})
            continue
        coverage_count += 1
        actual_rows = int(metadata["actual_rows"])
        if actual_rows < requested_lookback:
            short_history.append(
                {"code": code, "actual_rows": actual_rows, "reason": "short_history"}
            )
        latest_date = str(metadata["latest_date"])
        latest_dates[latest_date] = latest_dates.get(latest_date, 0) + 1
        source = str(metadata["source"])
        source_counts[source] = source_counts.get(source, 0) + 1
    total = len(codes)
    return {
        "cache_coverage_count": coverage_count,
        "cache_coverage_rate": coverage_count / total if total else 0.0,
        "short_history": short_history,
        "latest_date_distribution": latest_dates,
        "source_counts": source_counts,
        "invalid_or_missing_cache": invalid_or_missing,
    }


def _provider_errors(
    result: MarketCollectionResult, report: DailyCollectionReport
) -> list[str]:
    values = list(result.audit.get("provider_errors", []))
    values.extend(report.errors.values())
    return [_neutral_text(str(value)) for value in values]


def _cache_paths(root: Path) -> dict[str, str]:
    cache = root / "cache"
    return {
        "market_snapshot": str((cache / "market_snapshot.json").resolve()),
        "daily": str((cache / "daily").resolve()),
        "daily_checkpoint": str((cache / "daily_checkpoint.json").resolve()),
        "classification_map": str((root / "classification_map.csv").resolve()),
    }


def _file_records(run_dir: Path, rows: Mapping[str, int]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for key, count in rows.items():
        name = {
            "market_snapshot_csv": "market_snapshot.csv",
            "market_snapshot_json": "market_snapshot.json",
            "daily_indicators": "daily_indicators.csv",
            "classification_map": "classification_map.csv",
            "data_audit": "data_audit.json",
            "workflow_log": "workflow.log",
        }[key]
        path = run_dir / name
        result[key] = {
            "name": name,
            "rows": _line_count(path) if key == "workflow_log" else count,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return result


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline=None) as handle:
        return sum(1 for _ in handle)


def _error_summary(audit: Mapping[str, object]) -> dict[str, int]:
    daily = audit.get("daily", {})
    indicators = audit.get("indicators", {})
    classification = audit.get("classification", {})
    return {
        "daily": int(daily.get("failure_count", 0)) if isinstance(daily, dict) else 0,
        "indicators": int(indicators.get("failure_count", 0)) if isinstance(indicators, dict) else 0,
        "classification": len(classification.get("errors", [])) if isinstance(classification, dict) else 0,
    }


def _create_run_directory(root: Path, observed_at: datetime) -> Path:
    parent = root / observed_at.date().isoformat()
    parent.mkdir(parents=True, exist_ok=True)
    stem = f"{observed_at:%H%M%S}_objective_data"
    suffix = 1
    while True:
        name = stem if suffix == 1 else f"{stem}_{suffix}"
        path_option = parent / name
        try:
            path_option.mkdir(exist_ok=False)
        except FileExistsError:
            suffix += 1
            continue
        return path_option.resolve()


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_text_atomic(
        path,
        json.dumps(_neutralize_output(_json_value(payload)), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
    )


def _publish_latest(path: Path, payload: Mapping[str, object]) -> bool:
    generated_at = _parse_generated_at(payload.get("generated_at"))
    if generated_at is None:
        raise ValueError("latest handoff generated_at is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _LATEST_THREAD_LOCK, lock_path.open("a+b") as lock_handle:
        if lock_handle.seek(0, os.SEEK_END) == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        lock_handle.seek(0)
        _lock_file(lock_handle)
        try:
            existing_at = _existing_generated_at(path)
            if existing_at is not None and generated_at < existing_at:
                return False
            _write_json_atomic(path, payload)
            return True
        finally:
            _unlock_file(lock_handle)


def _lock_file(handle: Any) -> None:
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


def _unlock_file(handle: Any) -> None:
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


def _existing_generated_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_generated_at(payload.get("generated_at")) if isinstance(payload, dict) else None


def _parse_generated_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False
        ) as handle:
            _neutralize_frame(frame).to_csv(handle, index=False)
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return _json_value(frame.to_dict(orient="records"))


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if pd.isna(value):
        return None
    return value


def _neutralize_output(value: Any) -> Any:
    if isinstance(value, str):
        return _neutral_text(value)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            base = _neutral_text(key)
            key_option = base
            suffix = 2
            while key_option in normalized:
                key_option = f"{base}_{suffix}"
                suffix += 1
            normalized[key_option] = _neutralize_output(item)
        return normalized
    if isinstance(value, list):
        return [_neutralize_output(item) for item in value]
    return value


def _neutralize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    columns: list[str] = []
    for column in normalized.columns:
        base = _neutral_text(column)
        column_option = base
        suffix = 2
        while column_option in columns:
            column_option = f"{base}_{suffix}"
            suffix += 1
        columns.append(column_option)
    normalized.columns = columns
    for column in normalized.select_dtypes(include=["object", "string"]).columns:
        normalized[column] = normalized[column].map(
            lambda value: _neutral_text(value) if isinstance(value, str) else value
        )
    return normalized


def _neutral_text(value: object) -> str:
    text = str(value)
    for term in _FORBIDDEN_TERMS:
        text = re.sub(re.escape(term), "data", text, flags=re.IGNORECASE)
    return text


def _unique_codes(frame: pd.DataFrame) -> list[str]:
    if "code" not in frame:
        raise ValueError("market snapshot has no code column")
    codes = [str(value) for value in frame["code"]]
    if not codes or any(len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError("market snapshot has invalid codes")
    return list(dict.fromkeys(codes))


def _observed_at(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    return value.astimezone() if value.tzinfo is None else value


if __name__ == "__main__":
    raise SystemExit(main())

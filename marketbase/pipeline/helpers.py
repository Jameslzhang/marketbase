"""通用工具函数 —— 从 local_workflow.py 提取.

包含原子 I/O、JSON 序列化、文件锁、目录创建、字段常量等.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
import socket
import sys
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, IO, Mapping, cast

import pandas as pd

# ── Windows / POSIX 文件锁 ───────────────────────────────────────────
try:
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


# ── 指标字段定义 ─────────────────────────────────────────────────────
_INDICATOR_VALUE_FIELDS = (
    "ma5",
    "ma10",
    "ma11",
    "ma20",
    "ma23",
    "ma60",
    "ma120",
    "ma250",
    "rsi14",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "atr14",
    "atr14_pct",
    "boll_upper",
    "boll_middle",
    "boll_lower",
    "boll_position",
    "return_5d",
    "return_10d",
    "return_20d",
    "upper_shadow_ratio",
    "lower_shadow_ratio",
    "momentum_delta_1",
    "momentum_delta_3",
    "momentum_improving",
    "high_20d",
    "low_20d",
)
_INDICATOR_FIELDS = (
    *_INDICATOR_VALUE_FIELDS,
    "repeated_upper_shadow",
    "overheated",
    "rps20",
    "input_rows",
    "first_date",
    "last_date",
    "last_trade_date",
    "includes_intraday_today",
    "calculated_at",
)


# ── 文件锁 ───────────────────────────────────────────────────────────

def _try_lock_nonblocking(handle: IO[bytes]) -> bool:
    """尝试获取文件锁（非阻塞），成功返回 True."""
    if msvcrt is not None:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            return False
    return True


def _lock_file(handle: IO[bytes]) -> None:
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


def _unlock_file(handle: IO[bytes]) -> None:
    if msvcrt is not None:
        _ = handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


# ── JSON 工作流锁（替代二进制锁）────────────────────────────────────

def _write_workflow_lock(lock_path: Path) -> dict[str, object]:
    """Write a JSON lock file with process metadata.

    Uses atomic temp-file + os.replace() to prevent two processes from
    both passing the stale-lock check and then both writing their PID.
    """
    payload: dict[str, object] = {
        "pid": os.getpid(),
        "started_at": datetime.now().astimezone().isoformat(),
        "command": sys.argv,
        "hostname": socket.gethostname(),
    }
    tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, lock_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return payload


def _remove_workflow_lock(lock_path: Path) -> None:
    """Remove the JSON lock file if it exists."""
    lock_path.unlink(missing_ok=True)


def _check_and_clean_stale_lock(lock_path: Path, threshold_minutes: int = 30) -> bool:
    """Check if a JSON lock file is stale and clean it up.

    Returns:
        True if the lock was removed or doesn't exist (no valid lock held).
        False if the lock is valid and held by an active process.
    """
    if not lock_path.exists():
        return True

    try:
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        lock_path.unlink(missing_ok=True)
        return True

    if not isinstance(lock_data, dict):
        lock_path.unlink(missing_ok=True)
        return True

    pid = lock_data.get("pid")
    if pid is not None and isinstance(pid, int):
        try:
            os.kill(pid, 0)
            # PID exists — lock is valid
            return False
        except OSError:
            # PID doesn't exist — lock is stale, clean immediately
            lock_path.unlink(missing_ok=True)
            return True
    else:
        lock_path.unlink(missing_ok=True)
        return True

    return False


# ── 原子文件操作 ─────────────────────────────────────────────────────

def _atomic_replace(temp_path: Path, target_path: Path, *, retries: int = 5, delay: float = 0.1) -> None:
    """Atomically replace *target_path* with *temp_path*, retrying on Windows lock errors."""
    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False
        ) as handle:
            _neutralize_frame(frame).to_csv(handle, index=False)
            temporary = Path(handle.name)
        _atomic_replace(temporary, path)
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
            _ = handle.write(text)
            temporary = Path(handle.name)
        _atomic_replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_text_atomic(
        path,
        json.dumps(_neutralize_output(_json_value(payload)), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
    )


def _publish_latest(path: Path, payload: Mapping[str, object]) -> bool:
    import threading
    _LATEST_THREAD_LOCK = threading.Lock()
    generated_at = _parse_generated_at(payload.get("generated_at"))
    if generated_at is None:
        raise ValueError("latest handoff generated_at is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _LATEST_THREAD_LOCK, lock_path.open("a+b") as lock_handle:
        if lock_handle.seek(0, os.SEEK_END) == 0:
            _ = lock_handle.write(b"\0")
            lock_handle.flush()
        _ = lock_handle.seek(0)
        _lock_file(lock_handle)
        try:
            existing_at = _existing_generated_at(path)
            if existing_at is not None and generated_at < existing_at:
                return False
            _write_json_atomic(path, payload)
            return True
        finally:
            _unlock_file(lock_handle)


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


# ── 序列化 / 中性化 ─────────────────────────────────────────────────

def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return _json_value(frame.to_dict(orient="records"))


def _json_value(value: Any) -> Any:  # pyright: ignore[reportExplicitAny]
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


def _neutralize_output(value: Any) -> Any:  # pyright: ignore[reportExplicitAny]
    if isinstance(value, str):
        return _neutral_text(value)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]
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
    return str(value)


# ── 时间 / 目录 / 工具 ──────────────────────────────────────────────

def _observed_at(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    return value.astimezone() if value.tzinfo is None else value


def _unique_codes(frame: pd.DataFrame) -> list[str]:
    if "code" not in frame:
        raise ValueError("market snapshot has no code column")
    codes = [str(value) for value in frame["code"]]
    if not codes or any(len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError("market snapshot has invalid codes")
    return list(dict.fromkeys(codes))


def _create_run_directory(root: Path, observed_at: datetime, phase: str = "post_close") -> Path:
    parent = root / observed_at.date().isoformat()
    parent.mkdir(parents=True, exist_ok=True)
    phase_slug = _detect_session_slug(observed_at, phase)
    stem = f"{observed_at:%H%M%S}_{phase_slug}_objective_data"
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


def _detect_session_slug(observed_at: datetime, phase: str | None = None) -> str:
    """Auto-detect session phase slug from observed time.

    When phase is explicitly provided, validates it against actual time.
    If inconsistent (e.g. intraday_1300 at 21:41), auto-corrects to the
    real phase and emits a warning. The explicit phase is treated as a hint,
    not as authoritative override.

    Only returns: intraday_1300, intraday_1400, intraday_1430, lunch_break,
    post_close.
    """
    t = observed_at.time()
    morning_end = datetime.strptime("11:30", "%H:%M").time()
    afternoon_start = datetime.strptime("13:00", "%H:%M").time()
    close_1400 = datetime.strptime("14:00", "%H:%M").time()
    close_1430 = datetime.strptime("14:30", "%H:%M").time()
    close_1500 = datetime.strptime("15:00", "%H:%M").time()

    if t <= morning_end:
        actual_phase = "intraday_1300"
    elif t < afternoon_start:
        actual_phase = "lunch_break"
    elif afternoon_start <= t < close_1400:
        actual_phase = "intraday_1300"
    elif close_1400 <= t < close_1430:
        actual_phase = "intraday_1400"
    elif close_1430 <= t < close_1500:
        actual_phase = "intraday_1430"
    else:
        actual_phase = "post_close"

    if phase is not None and phase != actual_phase:
        import sys
        print(
            f"Warning: explicit phase '{phase}' inconsistent with actual time "
            f"({observed_at.strftime('%H:%M')}), auto-correcting to '{actual_phase}'",
            flush=True,
        )
        return actual_phase

    return phase if phase is not None else actual_phase


def _file_records(run_dir: Path, rows: Mapping[str, int]) -> dict[str, dict[str, object]]:
    now_iso = datetime.now().astimezone().isoformat()
    result: dict[str, dict[str, object]] = {}
    for key, count in rows.items():
        name = {
            "market_snapshot_csv": "market_snapshot.csv",
            "market_snapshot_json": "market_snapshot.json",
            "daily_indicators": "daily_indicators.csv",
            "classification_map": "classification_map.csv",
            "index_data": "index_data.csv",
            "industry_agg": "industry_agg.csv",
            "market_breadth": "market_breadth.json",
            "data_audit": "data_audit.json",
            "workflow_log": "workflow.log",
            "intraday_minutes_parquet": "intraday_minutes.parquet",
            "raw_snapshot_response": "raw_snapshot_response.json",
            "fields_md": "FIELDS.md",
        }[key]
        path = run_dir / name
        result[key] = {
            "name": name,
            "rows": _line_count(path) if key == "workflow_log" else count,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "generated_at": now_iso,
            "relative_path": name,
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


def _cache_paths(root: Path) -> dict[str, str]:
    cache = root / "cache"
    return {
        "market_snapshot": str((cache / "market_snapshot.json").resolve()),
        "daily": str((cache / "daily").resolve()),
        "daily_checkpoint": str((cache / "daily_checkpoint.json").resolve()),
        "classification_map": str((root / "classification_map.csv").resolve()),
        "supply_chain_map": str((root / "supply_chain_map.csv").resolve()),
    }


def _merge_indicators_to_snapshot(
    frame: pd.DataFrame, indicators_df: pd.DataFrame
) -> pd.DataFrame:
    """将日线指标列合并到快照 DataFrame 中."""
    if indicators_df.empty or "code" not in indicators_df.columns:
        return frame

    merge_fields = [f for f in _INDICATOR_VALUE_FIELDS if f in indicators_df.columns]
    if "rps20" in indicators_df.columns:
        merge_fields.append("rps20")

    if not merge_fields:
        return frame

    frame = frame.merge(
        indicators_df[["code", *merge_fields]],
        on="code",
        how="left",
        suffixes=("", "_ind"),
    )
    return frame

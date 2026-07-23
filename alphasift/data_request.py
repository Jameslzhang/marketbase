"""Validation and atomic persistence for the objective-data request protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
import tempfile
from typing import Any


_DAILY_FIELDS = frozenset({"raw", "ma", "rsi", "macd", "atr"})
_MINUTE_FIELDS = frozenset({"raw", "vwap"})
_CODE_PATTERN = re.compile(r"^[0-9]{6}$")
_TIME_PATTERN = re.compile(r"^[0-9]{2}:[0-9]{2}$")


@dataclass(frozen=True)
class DailyRequest:
    lookback: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class MinuteRequest:
    date: date
    start: str
    end: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class DataRequest:
    schema_version: int
    request_id: str
    codes: tuple[str, ...]
    daily: DailyRequest | None = None
    minute: MinuteRequest | None = None


def load_data_request(path: str | Path, *, today: date) -> DataRequest:
    """Load and strictly validate one JSON data request."""
    request_path = Path(path)
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取请求文件，请检查路径和 UTF-8 JSON 格式: {request_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("请求顶层必须是 JSON 对象")

    _reject_unknown(payload, {"schema_version", "request_id", "codes", "daily", "minute"}, "顶层")
    for field in ("schema_version", "request_id", "codes"):
        if field not in payload:
            raise ValueError(f"请求缺少必填字段 `{field}`，请补充后重试")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise ValueError("schema_version 必须为 1，请使用当前请求协议版本")
    request_id = payload["request_id"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id 不能为空，请提供可追踪的请求标识")
    codes = _validate_codes(payload["codes"])

    daily = _validate_daily(payload.get("daily")) if "daily" in payload else None
    minute = _validate_minute(payload.get("minute"), today) if "minute" in payload else None
    if daily is None and minute is None:
        raise ValueError("daily 与 minute 至少需要提供一个数据请求")
    return DataRequest(1, request_id.strip(), codes, daily, minute)


def write_data_response(path: str | Path, payload: dict[str, object]) -> Path:
    """Write a UTF-8 JSON response through a same-directory atomic replace."""
    if not isinstance(payload, dict):
        raise ValueError("响应内容必须是 JSON 对象")
    target = Path(path).expanduser().resolve()
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}-", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        with open(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
        temporary.replace(target)
        temporary = None
        return target
    except (OSError, TypeError, ValueError) as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise ValueError(f"写入响应失败，请检查目标路径和响应内容: {target}") from exc


def _reject_unknown(payload: dict[str, Any], allowed: set[str], scope: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{scope}包含未知字段 `{unknown[0]}`，请删除拼写错误的字段")


def _validate_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("codes 不能为空，必须提供六位数字 A 股代码列表")
    codes: list[str] = []
    seen: set[str] = set()
    for code in value:
        if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
            raise ValueError(f"代码 `{code}` 非法，必须是六位数字 A 股代码")
        if code in seen:
            raise ValueError(f"代码 `{code}` 重复，请保留唯一代码")
        seen.add(code)
        codes.append(code)
    return tuple(codes)


def _validate_daily(value: object) -> DailyRequest:
    if not isinstance(value, dict):
        raise ValueError("daily 必须是对象，请填写 lookback 和 fields")
    _reject_unknown(value, {"lookback", "fields"}, "daily")
    lookback = value.get("lookback")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 250:
        raise ValueError("daily.lookback 必须是 1 到 250 的整数")
    fields = _validate_fields(value.get("fields"), _DAILY_FIELDS, "daily.fields")
    return DailyRequest(lookback, fields)


def _validate_minute(value: object, today: date) -> MinuteRequest:
    if not isinstance(value, dict):
        raise ValueError("minute 必须是对象，请填写 date、start、end 和 fields")
    _reject_unknown(value, {"date", "start", "end", "fields"}, "minute")
    requested_date = value.get("date")
    if requested_date != today.isoformat():
        raise ValueError(f"minute.date 日期必须是今日 {today.isoformat()}，不能请求历史分钟数据")
    start = _validate_time(value.get("start"), "minute.start")
    end = _validate_time(value.get("end"), "minute.end")
    if start > end:
        raise ValueError("minute.start 开始时间不得晚于 minute.end，请调整时间范围")
    fields = _validate_fields(value.get("fields"), _MINUTE_FIELDS, "minute.fields")
    return MinuteRequest(today, start, end, fields)


def _validate_time(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        raise ValueError(f"{field} 时间必须使用 HH:MM 格式，例如 09:30")
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{field} 不是有效时间，请使用 00:00 到 23:59") from exc
    return value


def _validate_fields(value: object, allowed: frozenset[str], field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
    ):
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field} 不能为空，字段只能来自 {choices}")
    return tuple(value)

"""履行已验证的客观日线和当日分钟数据请求."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any

import pandas as pd

from marketbase.daily import fetch_daily_history
from marketbase.daily_collector import (
    _atomic_write_json,
    _cache_payload,
    _normalize_history,
    read_daily_cache,
)
from marketbase.data_request import DataRequest
from marketbase.live_workflow import fetch_tencent_minute_rows
from marketbase.indicators import compute_daily_indicators


_MINUTE_COLUMNS = ("time", "price", "volume", "amount")
_PROHIBITED_ERROR_TERMS = re.compile(
    r"candidate|recommend|buy|sell|signal|score|rank|probability|"
    r"候选|推荐|买入|卖出|信号|评分|排名|概率",
    re.IGNORECASE,
)


def parse_minute_rows(rows: Iterable[str]) -> pd.DataFrame:
    """解析腾讯累计分钟行，丢弃不可用记录."""
    parsed: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, str):
            continue
        parts = row.split()
        if len(parts) != 4:
            continue
        raw_time, raw_price, raw_volume, raw_amount = parts
        normalized_time = _normalize_minute_time(raw_time)
        if normalized_time is None:
            continue
        try:
            price = float(raw_price)
            volume = float(raw_volume)
            amount = float(raw_amount)
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in (price, volume, amount)):
            continue
        if volume < 0 or amount < 0:
            continue
        parsed.append(
            {
                "time": normalized_time,
                "price": price,
                "volume": volume,
                "amount": amount,
            }
        )
    if not parsed:
        return pd.DataFrame(columns=_MINUTE_COLUMNS)
    frame = pd.DataFrame(parsed, columns=_MINUTE_COLUMNS)
    return (
        frame.sort_values("time", kind="stable")
        .drop_duplicates(subset="time", keep="last")
        .reset_index(drop=True)
    )


def slice_minute_interval(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Return the inclusive requested minute interval in its normalized shape."""
    if not set(_MINUTE_COLUMNS).issubset(frame.columns):
        raise ValueError("minute frame has an invalid shape")
    return frame.loc[(frame["time"] >= start) & (frame["time"] <= end), _MINUTE_COLUMNS].reset_index(
        drop=True
    )


def collect_requested_data(
    request: DataRequest,
    *,
    daily_cache_root: str | Path,
    minute_fetcher: Callable[[str], list[str]] = fetch_tencent_minute_rows,
    daily_fetcher: Callable[..., pd.DataFrame] = fetch_daily_history,
    now: datetime | None = None,
) -> dict[str, object]:
    """Collect only the daily and minute scopes explicitly present in ``request``."""
    observed_at = _observed_at(now)
    audit: dict[str, dict[str, Any]] = {
        "requested": {"daily": 0, "minute": 0},
        "success": {"daily": 0, "minute": 0},
        "failed": {"daily": 0, "minute": 0},
        "rows": {"daily": 0, "minute": 0},
        "sources": {"daily": {}, "minute": {}},
    }
    data: dict[str, object] = {code: {} for code in request.codes}
    errors: list[dict[str, str]] = []
    cache_root = Path(daily_cache_root)

    for code in request.codes:
        code_data = data[code]
        assert isinstance(code_data, dict)
        if request.daily is not None:
            audit["requested"]["daily"] += 1
            try:
                daily_data, rows, source = _collect_daily_scope(
                    code,
                    request.daily.lookback,
                    request.daily.fields,
                    cache_root=cache_root,
                    daily_fetcher=daily_fetcher,
                    observed_at=observed_at,
                )
                code_data["daily"] = daily_data
                audit["success"]["daily"] += 1
                audit["rows"]["daily"] += rows
                _increment_source(audit["sources"]["daily"], source)
            except Exception:  # noqa: BLE001 - scope errors are isolated by protocol.
                audit["failed"]["daily"] += 1
                errors.append(_error_record(code, "daily", "daily", "daily data unavailable", observed_at))

        if request.minute is not None:
            audit["requested"]["minute"] += 1
            try:
                minute_data, rows = _collect_minute_scope(
                    code,
                    request.minute.date.isoformat(),
                    request.minute.start,
                    request.minute.end,
                    request.minute.fields,
                    minute_fetcher=minute_fetcher,
                    observed_at=observed_at,
                )
                code_data["minute"] = minute_data
                audit["success"]["minute"] += 1
                audit["rows"]["minute"] += rows
                _increment_source(audit["sources"]["minute"], "tencent")
            except _MinuteScopeError as exc:
                audit["failed"]["minute"] += 1
                errors.append(_error_record(code, "minute", "tencent", str(exc), observed_at))
            except Exception:  # noqa: BLE001 - scope errors are isolated by protocol.
                audit["failed"]["minute"] += 1
                errors.append(_error_record(code, "minute", "tencent", "minute data unavailable", observed_at))

    return _strict_json_value({
        "schema_version": 1,
        "request_id": request.request_id,
        "generated_at": observed_at.isoformat(),
        "volume_unit": "shares",
        "data": data,
        "errors": errors,
        "audit": audit,
    })


def _collect_daily_scope(
    code: str,
    lookback: int,
    fields: tuple[str, ...],
    *,
    cache_root: Path,
    daily_fetcher: Callable[..., pd.DataFrame],
    observed_at: datetime,
) -> tuple[dict[str, object], int, str]:
    cache_path = cache_root / f"{code}.json"
    required_lookback = max(lookback, 260)
    source = ""
    try:
        frame, metadata = read_daily_cache(cache_path)
        if metadata.get("code") != code:
            raise ValueError("daily cache code does not match request")
        if metadata.get("trading_date") != observed_at.date().isoformat():
            raise ValueError("daily cache is not from the current trading date")
        if int(metadata["requested_lookback"]) < required_lookback:
            raise ValueError("daily cache is insufficient")
        source = str(metadata["source"])
    except ValueError:
        history = daily_fetcher(code, lookback_days=required_lookback, source="auto", retries=2)
        frame = _normalize_history(history, required_lookback)
        source = str(history.attrs.get("daily_source") or "").strip()
        if not source:
            raise ValueError("daily source is missing")
        _atomic_write_json(
            cache_path,
            _cache_payload(
                code=code,
                frame=frame,
                trading_date=observed_at.date().isoformat(),
                lookback=required_lookback,
                fetched_at=observed_at,
                source=source,
                source_errors=[str(item) for item in history.attrs.get("source_errors", [])],
            ),
        )

    response: dict[str, object] = {}
    if "raw" in fields:
        response["raw"] = json.loads(frame.tail(lookback).to_json(orient="records"))
    indicators = compute_daily_indicators(frame, calculated_at=observed_at, trading_date=observed_at.date().isoformat())
    if "ma" in fields:
        response["ma"] = {key: indicators[key] for key in ("ma5", "ma10", "ma20", "ma60", "ma120", "ma250")}
    if "rsi" in fields:
        response["rsi"] = {"rsi14": indicators["rsi14"]}
    if "macd" in fields:
        response["macd"] = {
            "dif": indicators["macd_dif"],
            "dea": indicators["macd_dea"],
            "hist": indicators["macd_hist"],
        }
    if "atr" in fields:
        response["atr"] = {"atr14": indicators["atr14"], "atr14_pct": indicators["atr14_pct"]}
    return response, len(frame), source


def _collect_minute_scope(
    code: str,
    requested_date: str,
    start: str,
    end: str,
    fields: tuple[str, ...],
    *,
    minute_fetcher: Callable[[str], list[str]],
    observed_at: datetime,
) -> tuple[dict[str, object], int]:
    if requested_date != observed_at.date().isoformat():
        raise _MinuteScopeError("minute request date does not match current date")
    interval = slice_minute_interval(parse_minute_rows(minute_fetcher(code)), start, end)
    if interval.empty:
        raise _MinuteScopeError("no minute rows in requested interval")
    response: dict[str, object] = {}
    if "raw" in fields:
        response["raw"] = interval.to_dict("records")
    if "vwap" in fields:
        latest = interval.iloc[-1]
        volume = float(latest["volume"])
        response["vwap"] = None if volume == 0 else float(latest["amount"]) / (volume * 100)
    return response, len(interval)


def _normalize_minute_time(value: str) -> str | None:
    if len(value) != 4 or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, "%H%M").strftime("%H:%M")
    except ValueError:
        return None


def _observed_at(now: datetime | None) -> datetime:
    observed = now if now is not None else datetime.now().astimezone()
    return observed.astimezone() if observed.tzinfo is None else observed


def _increment_source(target: dict[str, int], source: str) -> None:
    target[source] = target.get(source, 0) + 1


def _error_record(
    code: str,
    scope: str,
    source: str,
    message: str,
    observed_at: datetime,
) -> dict[str, str]:
    return {
        "code": code,
        "scope": scope,
        "source": source,
        "error": _neutral_error(message),
        "observed_at": observed_at.isoformat(),
    }


def _neutral_error(message: str) -> str:
    return _PROHIBITED_ERROR_TERMS.sub("data", message)


def _strict_json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _strict_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


class _MinuteScopeError(ValueError):
    """Expected minute-request condition that is safe to return to the caller."""

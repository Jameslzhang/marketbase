from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from marketbase.data_request import DataRequest, DailyRequest, MinuteRequest
from marketbase.minute_collector import (
    collect_requested_data,
    parse_minute_rows,
    slice_minute_interval,
)


NOW = datetime(2026, 7, 22, 10, 0, tzinfo=timezone(timedelta(hours=8)))
TODAY = NOW.date()


def _request(*, codes=("600519",), daily=None, minute=None) -> DataRequest:
    return DataRequest(
        schema_version=1,
        request_id="task-6",
        codes=codes,
        daily=daily,
        minute=minute,
    )


def _minute_request(*, fields=("raw", "vwap"), start="09:31", end="09:32") -> MinuteRequest:
    return MinuteRequest(date=TODAY, start=start, end=end, fields=fields)


def _daily_rows(rows: int = 250) -> list[dict[str, object]]:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    return [
        {
            "date": value.date().isoformat(),
            "open": float(index + 1),
            "high": float(index + 2),
            "low": float(index),
            "close": float(index + 1),
            "volume": float(1_000 + index),
            "amount": float((1_000 + index) * (index + 1)),
        }
        for index, value in enumerate(dates)
    ]


def _write_daily_cache(cache_root, code: str, rows: list[dict[str, object]]) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "code": code,
        "fetched_at": NOW.isoformat(),
        "trading_date": TODAY.isoformat(),
        "requested_lookback": 260,
        "actual_rows": len(rows),
        "latest_date": rows[-1]["date"],
        "source": "fixture",
        "source_errors": [],
        "volume_unit": "shares",
        "rows": rows,
    }
    (cache_root / f"{code}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_parse_minute_rows_discards_invalid_rows_sorts_and_keeps_last_duplicate():
    frame = parse_minute_rows(
        [
            "0932 10.20 300 306000",
            "0931 10.10 200 202000",
            "0931 10.15 210 213150",
            "bad row",
            "2460 10.00 1 1000",
            "0933 nope 1 1000",
            "0934 10.00 -1 1000",
            "0935 10.00 1 -1000",
        ]
    )

    assert frame.to_dict("records") == [
        {"time": "09:31", "price": 10.15, "volume": 210.0, "amount": 213150.0},
        {"time": "09:32", "price": 10.2, "volume": 300.0, "amount": 306000.0},
    ]


def test_slice_minute_interval_includes_both_boundaries():
    frame = pd.DataFrame(
        {
            "time": ["09:30", "09:31", "09:32", "09:33"],
            "price": [10.0, 10.1, 10.2, 10.3],
            "volume": [100.0, 200.0, 300.0, 400.0],
            "amount": [100000.0, 202000.0, 306000.0, 412000.0],
        }
    )

    result = slice_minute_interval(frame, "09:31", "09:32")

    assert result["time"].tolist() == ["09:31", "09:32"]


def test_requested_minute_response_contains_only_requested_interval_and_fields(tmp_path):
    request = _request(minute=_minute_request())

    response = collect_requested_data(
        request,
        daily_cache_root=tmp_path,
        minute_fetcher=lambda code: [
            "0930 10.00 100 100000",
            "0931 10.10 200 202000",
            "0932 10.20 300 306000",
            "0933 10.30 400 412000",
        ],
        now=NOW,
    )

    minute = response["data"]["600519"]["minute"]
    assert list(minute) == ["raw", "vwap"]
    assert [row["time"] for row in minute["raw"]] == ["09:31", "09:32"]
    assert minute["vwap"] == pytest.approx(10.2)
    assert {"above_vwap", "hold", "activity", "signal"}.isdisjoint(minute)


def test_minute_date_defense_and_empty_interval_are_code_scope_errors(tmp_path):
    response = collect_requested_data(
        _request(codes=("600519", "000001"), minute=_minute_request()),
        daily_cache_root=tmp_path,
        minute_fetcher=lambda code: ["0930 10.00 100 100000"] if code == "600519" else [],
        now=datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc),
    )

    assert list(response["data"]) == ["600519", "000001"]
    assert response["data"] == {"600519": {}, "000001": {}}
    assert [error["code"] for error in response["errors"]] == ["600519", "000001"]
    assert {error["scope"] for error in response["errors"]} == {"minute"}
    assert {error["error"] for error in response["errors"]} == {"minute request date does not match current date"}

    empty = collect_requested_data(
        _request(minute=_minute_request()),
        daily_cache_root=tmp_path,
        minute_fetcher=lambda code: ["0930 10.00 100 100000"],
        now=NOW,
    )
    assert empty["errors"][0]["error"] == "no minute rows in requested interval"


def test_daily_cache_hit_returns_tail_requested_groups_and_neutral_indicators(tmp_path):
    _write_daily_cache(tmp_path, "600519", _daily_rows())
    request = _request(daily=DailyRequest(lookback=5, fields=("raw", "ma", "rsi", "macd", "atr")))

    response = collect_requested_data(
        request,
        daily_cache_root=tmp_path,
        daily_fetcher=lambda *args, **kwargs: pytest.fail("cache hit must not fetch"),
        now=NOW,
    )

    daily = response["data"]["600519"]["daily"]
    assert list(daily) == ["raw", "ma", "rsi", "macd", "atr"]
    assert len(daily["raw"]) == 5
    assert daily["raw"][0]["date"] == "2025-09-03"
    assert set(daily["ma"]) == {"ma5", "ma10", "ma20", "ma60", "ma120", "ma250"}
    assert set(daily["rsi"]) == {"rsi14"}
    assert set(daily["macd"]) == {"dif", "dea", "hist"}
    assert set(daily["atr"]) == {"atr14", "atr14_pct"}
    assert daily["ma"]["ma250"] == pytest.approx(125.5)
    assert daily["rsi"]["rsi14"] == 100.0
    assert {"signal", "score", "rank"}.isdisjoint(daily)
    assert response["audit"]["rows"]["daily"] == 250
    assert response["audit"]["sources"]["daily"] == {"fixture": 1}


def test_daily_cache_miss_fetches_only_requested_code_and_writes_compatible_cache(tmp_path):
    calls: list[tuple[str, int, str, int]] = []

    def fetcher(code, *, lookback_days, source, retries):
        calls.append((code, lookback_days, source, retries))
        result = pd.DataFrame(_daily_rows(3))
        result.attrs["daily_source"] = "fixture"
        result.attrs["source_errors"] = []
        return result

    response = collect_requested_data(
        _request(codes=("600519",), daily=DailyRequest(lookback=2, fields=("raw",))),
        daily_cache_root=tmp_path,
        daily_fetcher=fetcher,
        now=NOW,
    )

    assert calls == [("600519", 260, "auto", 2)]
    cached = json.loads((tmp_path / "600519.json").read_text(encoding="utf-8"))
    assert cached["requested_lookback"] == 260
    assert cached["actual_rows"] == 3
    assert len(response["data"]["600519"]["daily"]["raw"]) == 2


def test_daily_cache_with_another_code_is_rejected_and_refetched(tmp_path):
    _write_daily_cache(tmp_path, "000001", _daily_rows())
    (tmp_path / "000001.json").replace(tmp_path / "600519.json")
    calls: list[str] = []

    def fetcher(code, **kwargs):
        calls.append(code)
        result = pd.DataFrame(_daily_rows(3))
        result.attrs["daily_source"] = "fixture"
        result.attrs["source_errors"] = []
        return result

    response = collect_requested_data(
        _request(daily=DailyRequest(lookback=2, fields=("raw",))),
        daily_cache_root=tmp_path,
        daily_fetcher=fetcher,
        now=NOW,
    )

    assert calls == ["600519"]
    assert response["data"]["600519"]["daily"]["raw"]
    assert json.loads((tmp_path / "600519.json").read_text(encoding="utf-8"))["code"] == "600519"


def test_daily_cache_from_another_trading_date_is_refetched_only_for_requested_code(tmp_path):
    _write_daily_cache(tmp_path, "600519", _daily_rows())
    _write_daily_cache(tmp_path, "000001", _daily_rows())
    stale_path = tmp_path / "600519.json"
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["trading_date"] = "2026-07-21"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    calls: list[str] = []

    def fetcher(code, **kwargs):
        calls.append(code)
        result = pd.DataFrame(_daily_rows(3))
        result.attrs["daily_source"] = "fixture"
        result.attrs["source_errors"] = []
        return result

    response = collect_requested_data(
        _request(
            codes=("600519", "000001"),
            daily=DailyRequest(lookback=2, fields=("raw",)),
        ),
        daily_cache_root=tmp_path,
        daily_fetcher=fetcher,
        now=NOW,
    )

    assert calls == ["600519"]
    assert set(response["data"]) == {"600519", "000001"}
    assert json.loads(stale_path.read_text(encoding="utf-8"))["trading_date"] == TODAY.isoformat()


def test_daily_and_minute_failures_are_isolated_per_scope_and_code(tmp_path):
    _write_daily_cache(tmp_path, "000001", _daily_rows())
    request = _request(
        codes=("600519", "000001"),
        daily=DailyRequest(lookback=2, fields=("raw",)),
        minute=_minute_request(fields=("raw",)),
    )

    def daily_fetcher(code, **kwargs):
        raise RuntimeError("candidate recommend buy sell signal score rank probability")

    def minute_fetcher(code):
        if code == "000001":
            raise ConnectionError("candidate recommend buy sell signal score rank probability")
        return ["0931 10.10 200 202000"]

    response = collect_requested_data(
        request,
        daily_cache_root=tmp_path,
        daily_fetcher=daily_fetcher,
        minute_fetcher=minute_fetcher,
        now=NOW,
    )

    assert list(response["data"]) == ["600519", "000001"]
    assert "minute" in response["data"]["600519"]
    assert "daily" in response["data"]["000001"]
    assert [(error["code"], error["scope"]) for error in response["errors"]] == [
        ("600519", "daily"),
        ("000001", "minute"),
    ]
    errors = " ".join(error["error"] for error in response["errors"]).lower()
    assert not any(word in errors for word in ("candidate", "recommend", "buy", "sell", "signal", "score", "rank", "probability"))
    assert response["audit"]["requested"] == {"daily": 2, "minute": 2}
    assert response["audit"]["success"] == {"daily": 1, "minute": 1}
    assert response["audit"]["failed"] == {"daily": 1, "minute": 1}


def test_response_keeps_code_order_and_is_json_serializable(tmp_path):
    response = collect_requested_data(
        _request(codes=("000001", "600519"), minute=_minute_request(fields=("vwap",))),
        daily_cache_root=tmp_path,
        minute_fetcher=lambda code: ["0931 10.10 200 202000"],
        now=NOW,
    )

    assert list(response) == ["schema_version", "request_id", "generated_at", "volume_unit", "data", "errors", "audit"]
    assert list(response["data"]) == ["000001", "600519"]
    assert list(response["data"]["000001"]["minute"]) == ["vwap"]
    assert response["audit"]["rows"]["minute"] == 2
    assert datetime.fromisoformat(response["generated_at"]).tzinfo is not None
    json.dumps(response, allow_nan=False)


def test_response_replaces_non_finite_indicator_values_with_null(tmp_path):
    rows = _daily_rows()
    rows[-1]["close"] = float("inf")
    _write_daily_cache(tmp_path, "600519", rows)

    response = collect_requested_data(
        _request(daily=DailyRequest(lookback=250, fields=("ma", "rsi", "macd", "atr"))),
        daily_cache_root=tmp_path,
        now=NOW,
    )

    encoded = json.dumps(response, allow_nan=False)
    assert "Infinity" not in encoded
    assert "NaN" not in encoded

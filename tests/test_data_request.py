import json
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import date
from pathlib import Path

import pytest

from marketbase.data_request import (
    DataRequest,
    DailyRequest,
    MinuteRequest,
    load_data_request,
    write_data_response,
)


TODAY = date(2026, 7, 22)


def _request(**overrides):
    payload = {
        "schema_version": 1,
        "request_id": "req-中文-1",
        "codes": ["000001", "600000"],
        "daily": {"lookback": 120, "fields": ["raw", "ma", "rsi"]},
        "minute": {
            "date": TODAY.isoformat(),
            "start": "09:30",
            "end": "15:00",
            "fields": ["raw", "vwap"],
        },
    }
    payload.update(overrides)
    return payload


def _write_request(tmp_path, payload):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_complete_request_returns_frozen_dataclasses(tmp_path):
    result = load_data_request(_write_request(tmp_path, _request()), today=TODAY)

    assert is_dataclass(result)
    assert isinstance(result, DataRequest)
    assert result.schema_version == 1
    assert result.request_id == "req-中文-1"
    assert result.codes == ("000001", "600000")
    assert result.daily == DailyRequest(120, ("raw", "ma", "rsi"))
    assert result.minute == MinuteRequest(TODAY, "09:30", "15:00", ("raw", "vwap"))
    with pytest.raises(FrozenInstanceError):
        result.request_id = "changed"


@pytest.mark.parametrize(
    "section",
    ["daily", "minute"],
)
def test_load_allows_only_one_request_type(tmp_path, section):
    payload = _request()
    payload.pop("daily" if section == "minute" else "minute")
    result = load_data_request(_write_request(tmp_path, payload), today=TODAY)
    assert getattr(result, section) is not None


@pytest.mark.parametrize(
    "codes",
    [["000001", "000001"], ["12345"], ["00000x"], [""], [None]],
)
def test_rejects_invalid_codes(tmp_path, codes):
    with pytest.raises(ValueError, match="代码"):
        load_data_request(_write_request(tmp_path, _request(codes=codes)), today=TODAY)


@pytest.mark.parametrize(
    "payload, message",
    [
        (_request(schema_version=2), "schema_version"),
        (_request(request_id=""), "request_id"),
        (_request(codes=[]), "codes"),
        ({"schema_version": 1, "request_id": "x", "codes": ["000001"]}, "daily"),
        (_request(extra=True), "未知"),
    ],
)
def test_rejects_invalid_top_level_request(tmp_path, payload, message):
    with pytest.raises(ValueError, match=message):
        load_data_request(_write_request(tmp_path, payload), today=TODAY)


@pytest.mark.parametrize(
    "daily, message",
    [
        ({"lookback": 0, "fields": ["raw"]}, "lookback"),
        ({"lookback": 251, "fields": ["raw"]}, "lookback"),
        ({"lookback": 10, "fields": []}, "fields"),
        ({"lookback": 10, "fields": ["close"]}, "fields"),
        ({"lookback": 10, "fields": ["raw"], "oops": 1}, "未知"),
    ],
)
def test_rejects_invalid_daily_section(tmp_path, daily, message):
    with pytest.raises(ValueError, match=message):
        load_data_request(_write_request(tmp_path, _request(daily=daily)), today=TODAY)


@pytest.mark.parametrize(
    "minute, message",
    [
        ({"date": "2026-07-21", "start": "09:30", "end": "10:00", "fields": ["raw"]}, "日期"),
        ({"date": TODAY.isoformat(), "start": "9:30", "end": "10:00", "fields": ["raw"]}, "时间"),
        ({"date": TODAY.isoformat(), "start": "10:01", "end": "10:00", "fields": ["raw"]}, "开始"),
        ({"date": TODAY.isoformat(), "start": "09:30", "end": "10:00", "fields": ["close"]}, "fields"),
        ({"date": TODAY.isoformat(), "start": "09:30", "end": "10:00", "fields": ["raw"], "oops": 1}, "未知"),
    ],
)
def test_rejects_invalid_minute_section(tmp_path, minute, message):
    with pytest.raises(ValueError, match=message):
        load_data_request(_write_request(tmp_path, _request(minute=minute)), today=TODAY)


def test_write_response_creates_parent_and_preserves_chinese(tmp_path):
    target = tmp_path / "nested" / "response.json"
    result = write_data_response(target, {"消息": "处理完成", "value": 1})

    assert result == target.resolve()
    assert json.loads(target.read_text(encoding="utf-8"))["消息"] == "处理完成"
    assert "\\u5904" not in target.read_text(encoding="utf-8")
    assert list(target.parent.glob(".*.tmp")) == []


def test_write_response_atomically_overwrites_existing_file(tmp_path):
    target = tmp_path / "response.json"
    target.write_text('{"old": true}', encoding="utf-8")
    write_data_response(target, {"new": "内容"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": "内容"}


def test_write_failure_cleans_temp_and_keeps_old_response(tmp_path, monkeypatch):
    target = tmp_path / "response.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(self, destination):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(ValueError, match="写入响应失败"):
        write_data_response(target, {"new": "内容"})
    assert target.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_write_response_rejects_non_finite_values_without_replacing_existing_file(tmp_path, value):
    target = tmp_path / "response.json"
    target.write_text('{"old": true}', encoding="utf-8")

    with pytest.raises(ValueError, match="\u5199\u5165\u54cd\u5e94\u5931\u8d25"):
        write_data_response(target, {"value": value})

    assert target.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob(".*.tmp")) == []

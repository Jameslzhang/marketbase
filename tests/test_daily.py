from __future__ import annotations

import json
import time

import pandas as pd
import pytest

import alphasift.daily as daily
from alphasift.daily import daily_source_health_snapshot, fetch_daily_history


@pytest.fixture(autouse=True)
def clear_daily_source_health():
    daily._SOURCE_HEALTH.clear()
    yield
    daily._SOURCE_HEALTH.clear()


def _history(source: str = "test") -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": ["2026-07-21", "2026-07-22"],
            "open": [10.0, 10.2],
            "high": [10.3, 10.5],
            "low": [9.9, 10.1],
            "close": [10.2, 10.4],
            "volume": [1000.0, 1200.0],
            "amount": [10100.0, 12480.0],
        }
    )
    frame.attrs["daily_source"] = source
    return frame


def test_fetch_daily_history_retries_transient_source_errors(monkeypatch):
    calls = 0

    def fetch(code, *, lookback_days):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return _history()

    monkeypatch.setattr(daily, "_fetch_daily_akshare", fetch)
    monkeypatch.setattr(daily.time, "sleep", lambda _seconds: None)

    result = fetch_daily_history("600519", source="akshare", retries=2)

    assert calls == 3
    assert result.attrs["daily_source"] == "akshare"
    assert len(result) == 2


def test_fetch_daily_history_auto_records_fallback_errors(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_API_TOKEN", raising=False)
    monkeypatch.setattr(daily, "_fetch_daily_tencent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(daily, "_fetch_daily_sina", lambda *a, **k: _history())
    monkeypatch.setattr(daily.time, "sleep", lambda _seconds: None)

    result = fetch_daily_history("000001", source="auto", retries=0)

    assert result.attrs["daily_source"] == "sina"
    assert result.attrs["daily_requested_source"] == "auto"
    assert result.attrs["source_errors"] == ["tencent after 1 attempts: down"]


def test_fetch_daily_history_rejects_non_a_share_source():
    with pytest.raises(ValueError, match="Unsupported daily source"):
        fetch_daily_history("AAPL", source="yfinance", retries=0)


def test_fetch_daily_history_uses_fresh_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    calls = 0

    def fetch(code, *, lookback_days):
        nonlocal calls
        calls += 1
        return _history()

    monkeypatch.setattr(daily, "_fetch_daily_akshare", fetch)
    first = fetch_daily_history("600000", source="akshare", cache_dir=cache_dir)
    second = fetch_daily_history("600000", source="akshare", cache_dir=cache_dir)

    assert calls == 1
    pd.testing.assert_frame_equal(first, second)


def test_fetch_daily_history_uses_stale_cache_after_sources_fail(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(daily, "_fetch_daily_akshare", lambda *a, **k: _history())
    fetch_daily_history("600000", source="akshare", cache_dir=cache_dir)
    monkeypatch.setattr(daily, "_fetch_daily_akshare", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(daily.time, "sleep", lambda _seconds: None)

    result = fetch_daily_history(
        "600000",
        source="akshare",
        retries=0,
        cache_dir=cache_dir,
        cache_ttl_seconds=0,
    )

    assert result.attrs["daily_stale"] is True
    assert "down" in result.attrs["source_errors"][0]


def test_daily_source_health_temporarily_disables_repeated_failures(monkeypatch):
    monkeypatch.setattr(daily, "_SOURCE_HEALTH_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(daily, "_SOURCE_HEALTH_COOLDOWN_SECONDS", 60)
    monkeypatch.setattr(daily, "_fetch_daily_akshare", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    for _ in range(2):
        with pytest.raises(RuntimeError):
            fetch_daily_history("600000", source="akshare", retries=0)

    state = daily_source_health_snapshot()["akshare"]
    assert state["disabled"] is True
    assert state["total_failures"] == 2.0


def test_source_health_order_preserves_default_ties():
    sources = ("tencent", "sina", "akshare")
    ordered, notes = daily._order_daily_sources_by_health(sources)
    assert ordered == sources
    assert notes == []


def test_source_health_order_moves_disabled_source_later():
    daily._SOURCE_HEALTH["tencent"] = {
        "failures": 3.0,
        "disabled_until": time.monotonic() + 60,
    }
    ordered, notes = daily._order_daily_sources_by_health(("tencent", "sina"))
    assert ordered == ("sina", "tencent")
    assert notes


def test_fetch_daily_tencent_normalizes_rows(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"sh600000": {"qfqday": [["2026-07-22", "10", "10.2", "10.3", "9.9", "1000", "10200"]]}}}

    monkeypatch.setattr(daily.requests, "get", lambda *a, **k: Response())
    result = daily._fetch_daily_tencent("600000", lookback_days=1)
    assert result.to_dict("records") == [{
        "date": "2026-07-22", "open": 10, "close": 10.2, "high": 10.3,
        "low": 9.9, "volume": 1000, "amount": 10200,
    }]


def test_fetch_daily_sina_normalizes_and_sorts_rows(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"data": [
                {"day": "2026-07-22", "open": "10", "close": "10.2", "high": "10.3", "low": "9.9", "volume": "1000"},
                {"day": "2026-07-21", "open": "9", "close": "9.2", "high": "9.3", "low": "8.9", "volume": "900"},
            ]}}

    monkeypatch.setattr(daily.requests, "get", lambda *a, **k: Response())
    result = daily._fetch_daily_sina("000001", lookback_days=2)
    assert result["date"].tolist() == ["2026-07-21", "2026-07-22"]


@pytest.mark.parametrize(
    ("code", "expected"),
    (("600000", "sh.600000"), ("000001", "sz.000001")),
)
def test_to_baostock_code_handles_a_share_markets(code, expected):
    assert daily._to_baostock_code(code) == expected


@pytest.mark.parametrize("code", ("430047", "830001", "920001"))
def test_to_baostock_code_rejects_unsupported_bse_codes(code):
    with pytest.raises(ValueError, match="BSE"):
        daily._to_baostock_code(code)


@pytest.mark.parametrize(
    ("code", "expected"),
    (("600000", "600000.SH"), ("000001", "000001.SZ"), ("430047", "430047.BJ")),
)
def test_to_tushare_code_handles_exchange_suffixes(code, expected):
    assert daily._to_tushare_code(code) == expected


def test_daily_cache_payload_is_plain_json(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "_fetch_daily_akshare", lambda *a, **k: _history())
    fetch_daily_history("600000", source="akshare", cache_dir=tmp_path)
    payload = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["key"]["code"] == "600000"

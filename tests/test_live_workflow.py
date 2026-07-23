from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import alphasift.live_workflow as live


NOW = datetime.fromisoformat("2026-07-22T10:00:00+08:00")


def _quotes() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["600000", "000001", "430047"],
            "name": ["A", "B", "C"],
            "price": [10.0, 11.0, 12.0],
            "volume": [100.0, 200.0, 300.0],
        }
    )


def test_build_live_snapshot_keeps_quotes_and_fills_reference_fields():
    reference = pd.DataFrame(
        {
            "code": ["600000", "000001", "430047"],
            "price": [99.0, 99.0, 99.0],
            "turnover_rate": [1.0, 2.0, 3.0],
            "industry": ["I1", "I2", "I3"],
        }
    )
    result = live.build_live_snapshot(_quotes(), reference, now=NOW, min_rows=3)
    assert result["price"].tolist() == [10.0, 11.0, 12.0]
    assert result["turnover_rate"].tolist() == [1.0, 2.0, 3.0]
    assert result["symbol"].tolist() == ["sh600000", "sz000001", "bj430047"]


def test_build_live_snapshot_appends_reference_only_bse_rows():
    primary = _quotes().iloc[:2].copy()
    reference = _quotes().iloc[[2]].assign(turnover_rate=3.0)
    result = live.build_live_snapshot(primary, reference, now=NOW, min_rows=2)
    assert result["code"].tolist() == ["600000", "000001", "430047"]


def test_acquire_live_snapshot_retries_partial_primary():
    attempts = 0

    def primary():
        nonlocal attempts
        attempts += 1
        return _quotes().iloc[:1] if attempts == 1 else _quotes()

    result, report = live.acquire_live_snapshot(
        primary_fetcher=primary,
        reference_fetcher=pd.DataFrame,
        now=NOW,
        min_rows=3,
        attempts=2,
        required_markets=("sh", "sz", "bj"),
    )
    assert len(result) == 3
    assert report["primary_attempts"] == 2
    assert len(report["primary_errors"]) == 1


def test_acquire_live_snapshot_rejects_missing_required_market():
    with pytest.raises(ValueError, match="missing required markets"):
        live.acquire_live_snapshot(
            primary_fetcher=lambda: _quotes().iloc[:2],
            reference_fetcher=pd.DataFrame,
            now=NOW,
            min_rows=2,
            required_markets=("bj",),
        )


def test_reference_snapshot_falls_back_to_tencent_for_bse_rows():
    cached = pd.concat([_quotes()] * 100, ignore_index=True)
    cached["code"] = [f"43{index:04d}" for index in range(len(cached))]

    def fetcher(source):
        if source == "efinance":
            raise RuntimeError("down")
        return _quotes().iloc[:2]

    result = live.fetch_reference_snapshot_with_bse_fallback(
        cached,
        fetcher=fetcher,
        bse_fetcher=lambda frame: frame.assign(price=12.0, source="tencent_bse"),
        min_bse_rows=300,
    )
    assert len(result) == 302
    assert result.attrs["snapshot_source"] == "em_datacenter+tencent_bse"


def test_validate_live_snapshot_rejects_previous_trading_date_during_session():
    frame = _quotes().assign(timestamp="2026-07-21T10:00:00+08:00")
    with pytest.raises(ValueError, match="current trading date"):
        live.validate_live_snapshot_freshness(frame, now=NOW, min_rows=3)


def test_fetch_tencent_minute_rows_returns_uninterpreted_rows(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"sh600000": {"data": {"data": ["0930 10.0 1 10"]}}}}

    monkeypatch.setattr(live.requests, "get", lambda *a, **k: Response())
    assert live.fetch_tencent_minute_rows("600000") == ["0930 10.0 1 10"]


@pytest.mark.parametrize(
    ("code", "symbol"),
    [
        ("600000", "sh600000"),
        ("000001", "sz000001"),
        ("300001", "sz300001"),
        ("430047", "bj430047"),
        ("830047", "bj830047"),
        ("920047", "bj920047"),
    ],
)
def test_fetch_tencent_minute_rows_uses_market_specific_symbol(monkeypatch, code, symbol):
    received: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {symbol: {"data": {"data": ["0930 10.0 1 10"]}}}}

    def get(*args, **kwargs):
        received.append(kwargs["params"]["code"])
        return Response()

    monkeypatch.setattr(live.requests, "get", get)

    assert live.fetch_tencent_minute_rows(code) == ["0930 10.0 1 10"]
    assert received == [symbol]


@pytest.mark.parametrize("code", ["100000", "500000", "700000", "abc", "60000", "6000000"])
def test_fetch_tencent_minute_rows_rejects_invalid_code(code):
    with pytest.raises(ValueError, match="unsupported A-share code"):
        live.fetch_tencent_minute_rows(code)


def test_strategy_interpretation_helpers_are_not_exposed():
    removed = {
        "compute_minute_confirmation",
        "enrich_minute_confirmations",
        "enrich_stock_profiles",
        "fetch_eastmoney_stock_profile",
    }
    assert all(not hasattr(live, name) for name in removed)

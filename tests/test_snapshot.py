import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from marketbase.snapshot import (
    _configure_tushare_client,
    _eastmoney_get,
    _fetch_sina,
    _normalize,
    _prepare_tushare_snapshot,
    _source_health,
    fetch_cn_snapshot,
    fetch_snapshot_with_fallback,
    snapshot_source_health_snapshot,
)


@pytest.fixture(autouse=True)
def clear_snapshot_source_health():
    _source_health.reset()
    yield
    _source_health.reset()


def test_normalize_efinance_maps_pb_ratio():
    df = pd.DataFrame(
        [
            {
                "股票代码": "000001",
                "股票名称": "平安银行",
                "最新价": "10.00",
                "涨跌幅": "1.23",
                "成交额": "123456789",
                "总市值": "1000000000",
                "流通市值": "800000000",
                "动态市盈率": "5.2",
                "市净率": "0.8",
                "量比": "1.1",
                "换手率": "2.5",
                "所属行业": "银行",
                "概念题材": "中特估,低估值",
            }
        ]
    )

    normalized = _normalize(df, source="efinance")

    assert normalized.loc[0, "pb_ratio"] == 0.8
    assert normalized.loc[0, "pe_ratio"] == 5.2
    assert normalized.loc[0, "industry"] == "银行"
    assert normalized.loc[0, "concepts"] == "中特估,低估值"


def test_normalize_sina_maps_valuation_and_turnover_fields():
    df = pd.DataFrame([
        {
            "code": "000001",
            "name": "平安银行",
            "trade": "10.00",
            "changepercent": "1.23",
            "amount": "123456789",
            "mktcap": "1000000000",
            "nmc": "800000000",
            "per": "5.2",
            "pb": "0.8",
            "turnoverratio": "2.5",
        }
    ])

    normalized = _normalize(df, source="sina")

    assert normalized.loc[0, "code"] == "000001"
    assert normalized.loc[0, "price"] == 10.0
    assert normalized.loc[0, "change_pct"] == 1.23
    assert normalized.loc[0, "amount"] == pytest.approx(123456789)
    assert normalized.loc[0, "total_mv"] == pytest.approx(1000000000)
    assert normalized.loc[0, "circ_mv"] == pytest.approx(800000000)
    assert normalized.loc[0, "pe_ratio"] == 5.2
    assert normalized.loc[0, "pb_ratio"] == 0.8
    assert normalized.loc[0, "turnover_rate"] == 2.5
    assert normalized.attrs["snapshot_source"] == "sina"


def test_fetch_sina_paginates_and_normalizes_market_cap_units(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        if kwargs["params"]["node"] == "sz_a":
            return FakeResponse([])
        page = kwargs["params"]["page"]
        if page == 1:
            return FakeResponse([
                {
                    "code": "000001",
                    "name": "平安银行",
                    "trade": "10.00",
                    "changepercent": "1.23",
                    "amount": "123456789",
                    "mktcap": "100000",
                    "nmc": "80000",
                    "per": "5.2",
                    "pb": "0.8",
                    "turnoverratio": "2.5",
                }
            ])
        return FakeResponse([])

    monkeypatch.setattr("marketbase.snapshot.requests.get", fake_get)

    normalized = _fetch_sina()

    assert calls[0]["params"]["node"] == "sh_a"
    assert calls[0]["headers"]["Referer"] == "https://vip.stock.finance.sina.com.cn/mkt/"
    assert normalized.loc[0, "total_mv"] == pytest.approx(1000000000)
    assert normalized.loc[0, "circ_mv"] == pytest.approx(800000000)


def test_fetch_sina_does_not_treat_a_short_page_as_end_of_market(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def row(code):
        return {
            "code": code,
            "name": code,
            "trade": "10.00",
            "changepercent": "1.00",
            "amount": "1000000",
        }

    pages = {1: [row("000001")], 2: [row("000002")], 3: []}
    def fake_get(url, **kwargs):
        if kwargs["params"]["node"] == "sz_a":
            return FakeResponse([])
        return FakeResponse(pages.get(kwargs["params"]["page"], []))

    monkeypatch.setattr("marketbase.snapshot.requests.get", fake_get)

    normalized = _fetch_sina()

    assert normalized["code"].tolist() == ["000001", "000002"]


def test_fetch_sina_retries_failed_page_inside_nonempty_range(monkeypatch):
    attempts = {}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def row(code):
        return {
            "code": code,
            "name": code,
            "trade": "10.00",
            "changepercent": "1.00",
            "amount": "1000000",
        }

    def fake_get(url, **kwargs):
        node = kwargs["params"]["node"]
        page = kwargs["params"]["page"]
        attempts[(node, page)] = attempts.get((node, page), 0) + 1
        if node == "sz_a":
            return FakeResponse([])
        if page == 2 and attempts[(node, page)] == 1:
            raise ConnectionError("temporary 502")
        return FakeResponse({1: [row("600001")], 2: [row("600002")], 3: [row("600003")]}.get(page, []))

    monkeypatch.setattr("marketbase.snapshot.requests.get", fake_get)

    normalized = _fetch_sina()

    assert normalized["code"].tolist() == ["600001", "600002", "600003"]
    assert attempts[("sh_a", 2)] == 2


def test_eastmoney_get_reuses_session_and_throttles(monkeypatch):
    events = []

    class FakeResponse:
        def raise_for_status(self):
            events.append("raise")

    class FakeSession:
        def get(self, url, **kwargs):
            events.append((url, kwargs))
            return FakeResponse()

    times = iter([100.0, 100.1, 100.4])
    monkeypatch.setattr("marketbase.snapshot._EM_SESSION", FakeSession())
    monkeypatch.setattr("marketbase.snapshot._EM_LAST_REQUEST_AT", 99.95)
    monkeypatch.setattr("marketbase.snapshot.time.monotonic", lambda: next(times))
    monkeypatch.setattr("marketbase.snapshot.time.sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr("marketbase.snapshot.random.uniform", lambda start, end: 0.0)

    response = _eastmoney_get("https://example.test", params={"p": 1})

    assert isinstance(response, FakeResponse)
    assert events[0][0] == "sleep"
    assert events[0][1] == pytest.approx(0.95)
    assert events[1] == ("https://example.test", {"params": {"p": 1}})
    assert events[2] == "raise"


def test_prepare_tushare_snapshot_maps_fields_and_units():
    daily = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260430",
                "close": "10.00",
                "pct_chg": "1.23",
                "amount": "123456.789",
            }
        ]
    )
    daily_basic = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "turnover_rate": "2.5",
                "volume_ratio": "1.1",
                "pe": "5.2",
                "pb": "0.8",
                "total_mv": "100000",
                "circ_mv": "80000",
            }
        ]
    )
    stock_basic = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "平安银行",
                "industry": "银行",
            }
        ]
    )

    normalized = _prepare_tushare_snapshot(daily, daily_basic, stock_basic)

    assert normalized.loc[0, "code"] == "000001"
    assert normalized.loc[0, "name"] == "平安银行"
    assert normalized.loc[0, "price"] == 10.0
    assert normalized.loc[0, "change_pct"] == 1.23
    assert normalized.loc[0, "amount"] == pytest.approx(123456789)
    assert normalized.loc[0, "total_mv"] == pytest.approx(1000000000)
    assert normalized.loc[0, "circ_mv"] == pytest.approx(800000000)
    assert normalized.loc[0, "pe_ratio"] == 5.2
    assert normalized.loc[0, "pb_ratio"] == 0.8
    assert normalized.loc[0, "volume_ratio"] == 1.1
    assert normalized.loc[0, "turnover_rate"] == 2.5
    assert normalized.loc[0, "industry"] == "银行"
    assert normalized.attrs["snapshot_source"] == "tushare"


def test_fetch_tushare_requires_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        fetch_cn_snapshot("tushare")


def test_configure_tushare_client_reads_http_url(monkeypatch):
    class FakePro:
        pass

    monkeypatch.setenv("TUSHARE_API_URL", "http://example.test")
    pro = FakePro()

    _configure_tushare_client(pro, token="token")

    assert pro._DataApi__token == "token"
    assert pro._DataApi__http_url == "http://example.test"


def test_fetch_snapshot_with_fallback_attaches_source_errors(monkeypatch):
    def fake_fetch(source):
        if source == "bad":
            raise RuntimeError("bad source")
        return pd.DataFrame([{"code": "000001", "name": "示例", "price": 10.0}])

    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", fake_fetch)

    df = fetch_snapshot_with_fallback(["bad", "good"])

    assert df.attrs["source_errors"] == ["bad: bad source"]


def test_snapshot_source_health_temporarily_disables_repeated_failures(monkeypatch):
    monkeypatch.setattr("marketbase.snapshot.time.monotonic", lambda: 200.0)

    _source_health.record_failure("sina")
    _source_health.record_failure("sina")
    assert _source_health.disabled_reason("sina") is None
    _source_health.record_failure("sina")

    assert "temporarily disabled" in str(_source_health.disabled_reason("sina"))
    _source_health.record_success("sina")
    assert _source_health.disabled_reason("sina") is None


def test_fetch_snapshot_with_fallback_skips_disabled_sources(monkeypatch):
    calls = []

    def fake_fetch(source):
        calls.append(source)
        return pd.DataFrame([{"code": "000001", "name": "示例", "price": 10.0}])

    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", fake_fetch)
    monkeypatch.setattr("marketbase.snapshot.time.monotonic", lambda: 200.0)
    _source_health.record_failure("sina")
    _source_health.record_failure("sina")
    _source_health.record_failure("sina")

    df = fetch_snapshot_with_fallback(["sina", "efinance"])

    assert calls == ["efinance"]
    assert df.attrs["source_errors"][0].startswith("sina: temporarily disabled")


def test_snapshot_wrapper_timeout_falls_back_to_next_source(monkeypatch):
    def slow_efinance():
        time.sleep(0.05)
        return pd.DataFrame([{"code": "000001", "name": "慢源", "price": 10.0}])

    def fast_akshare():
        return pd.DataFrame([{"code": "000001", "name": "快源", "price": 11.0}])

    monkeypatch.setenv("MARKETBASE_SNAPSHOT_CALL_TIMEOUT_SEC", "0.001")
    monkeypatch.setattr("marketbase.snapshot._fetch_efinance", slow_efinance)
    monkeypatch.setattr("marketbase.snapshot._fetch_akshare_em", fast_akshare)

    df = fetch_snapshot_with_fallback(["efinance", "akshare_em"])
    health = snapshot_source_health_snapshot(["efinance", "akshare_em"])

    assert df.loc[0, "name"] == "快源"
    assert df.attrs["snapshot_source"] == "akshare_em"
    assert df.attrs["source_errors"][0].startswith("efinance: snapshot source efinance timed out")
    assert health["efinance"]["failures"] == 1.0
    assert "timed out" in health["efinance"]["last_error"]
    assert health["akshare_em"]["successes"] == 1.0
    assert health["akshare_em"]["last_rows"] == 1.0


def test_fetch_snapshot_with_fallback_skips_missing_required_columns(monkeypatch):
    def fake_fetch(source):
        if source == "missing_pb":
            return pd.DataFrame([{"code": "000001", "name": "示例", "price": 10.0}])
        return pd.DataFrame([{
            "code": "000001",
            "name": "示例",
            "price": 10.0,
            "pb_ratio": 0.8,
        }])

    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", fake_fetch)

    df = fetch_snapshot_with_fallback(
        ["missing_pb", "complete"],
        required_columns=["price", "pb_ratio"],
    )

    assert df.attrs["source_errors"] == [
        "missing_pb: missing required columns pb_ratio"
    ]
    assert df.loc[0, "pb_ratio"] == 0.8


def test_fetch_snapshot_with_fallback_saves_last_good_cache_on_live_success(
    monkeypatch,
    tmp_path,
):
    cache_path = tmp_path / "snapshot.last_good.json"

    def fake_fetch(source):
        df = pd.DataFrame([{
            "code": "000001",
            "name": "示例",
            "price": 10.0,
            "pb_ratio": 0.8,
        }])
        df.attrs["snapshot_source"] = source
        return df

    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", fake_fetch)

    df = fetch_snapshot_with_fallback(
        ["good"],
        required_columns=["price", "pb_ratio"],
        fallback_snapshot_path=cache_path,
    )

    assert df.attrs["snapshot_source"] == "good"
    assert df.attrs["fallback_used"] is False
    assert cache_path.is_file()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["snapshot_source"] == "good"
    assert payload["metadata"]["row_count"] == 1

    monkeypatch.setattr(
        "marketbase.snapshot.fetch_cn_snapshot",
        lambda source: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    cached = fetch_snapshot_with_fallback(
        ["good"],
        required_columns=["price", "pb_ratio"],
        fallback_snapshot_path=cache_path,
    )

    assert cached.loc[0, "code"] == "000001"
    assert cached.loc[0, "pb_ratio"] == 0.8


def test_fetch_snapshot_with_fallback_uses_last_good_cache_after_all_sources_fail(
    monkeypatch,
    tmp_path,
):
    cache_path = tmp_path / "snapshot.last_good.json"
    live = pd.DataFrame([{
        "code": "000001",
        "name": "示例",
        "price": 10.0,
        "pb_ratio": 0.8,
    }])
    live.attrs["snapshot_source"] = "good"
    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", lambda source: live)
    fetch_snapshot_with_fallback(
        ["good"],
        required_columns=["price", "pb_ratio"],
        fallback_snapshot_path=cache_path,
    )

    def fail(source):
        raise RuntimeError(f"{source} unavailable")

    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", fail)

    cached = fetch_snapshot_with_fallback(
        ["efinance", "akshare_em"],
        required_columns=["price", "pb_ratio"],
        fallback_snapshot_path=cache_path,
    )

    assert cached.attrs["snapshot_source"] == "last_good_cache"
    assert cached.attrs["fallback_used"] is True
    assert cached.attrs["source_errors"] == [
        "efinance: efinance unavailable",
        "akshare_em: akshare_em unavailable",
    ]
    assert cached.loc[0, "code"] == "000001"


def test_snapshot_fallback_marks_stale_source_metadata(monkeypatch, tmp_path):
    cache_path = tmp_path / "snapshot.last_good.json"
    live = pd.DataFrame([{
        "code": "000001",
        "name": "示例",
        "price": 10.0,
    }])
    live.attrs["snapshot_source"] = "good"
    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", lambda source: live)
    fetch_snapshot_with_fallback(["good"], fallback_snapshot_path=cache_path)

    created_at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["created_at"] = created_at
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        "marketbase.snapshot.fetch_cn_snapshot",
        lambda source: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    cached = fetch_snapshot_with_fallback(
        ["efinance"],
        fallback_snapshot_path=cache_path,
    )

    assert cached.attrs["snapshot_source"] == "last_good_cache"
    assert cached.attrs["fallback_used"] is True
    assert cached.attrs["stale"] is True
    assert cached.attrs["stale_age_hours"] == pytest.approx(3.0, abs=0.1)
    assert cached.attrs["source_errors"] == ["efinance: offline"]


def test_snapshot_fallback_rejects_cache_older_than_max_age(monkeypatch, tmp_path):
    cache_path = tmp_path / "snapshot.last_good.json"
    live = pd.DataFrame([{"code": "000001", "name": "示例", "price": 10.0}])
    live.attrs["snapshot_source"] = "good"
    monkeypatch.setattr("marketbase.snapshot.fetch_cn_snapshot", lambda source: live)
    fetch_snapshot_with_fallback(["good"], fallback_snapshot_path=cache_path)

    created_at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["created_at"] = created_at
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        "marketbase.snapshot.fetch_cn_snapshot",
        lambda source: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.raises(RuntimeError, match="last_good_cache: cache stale_age_hours=.*exceeds max_age_hours=1"):
        fetch_snapshot_with_fallback(
            ["efinance"],
            fallback_snapshot_path=cache_path,
            fallback_max_age_hours=1,
        )


def test_fetch_snapshot_with_fallback_raises_all_errors(monkeypatch):
    monkeypatch.setattr(
        "marketbase.snapshot.fetch_cn_snapshot",
        lambda source: (_ for _ in ()).throw(RuntimeError(source)),
    )

    with pytest.raises(RuntimeError, match="a: a; b: b"):
        fetch_snapshot_with_fallback(["a", "b"])

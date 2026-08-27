from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import fast_t1_scan


def test_parse_realtime_codes_normalizes_and_deduplicates():
    # 需求 7.2：按用户输入的股票顺序展示（不再排序）。
    assert fast_t1_scan.parse_realtime_codes("603986, 002594 603986") == [
        "603986",
        "002594",
    ]


def test_filter_realtime_quotes_preserves_requested_order_and_marks_missing():
    quotes = pd.DataFrame(
        [
            {"code": "603986", "name": "兆易创新", "price": 395.0, "change_pct": 1.2},
            {"code": "002594", "name": "比亚迪", "price": 300.0, "change_pct": -0.5},
        ]
    )
    result = fast_t1_scan.filter_realtime_quotes(["002594", "000001", "603986"], quotes)

    assert result["code"].tolist() == ["002594", "000001", "603986"]
    assert result.loc[result["code"] == "000001", "realtime_status"].iat[0] == "未找到"
    assert result.loc[result["code"] == "603986", "realtime_status"].iat[0] == "已获取"


def test_load_bse_codes_from_local_universe(tmp_path: Path):
    cache = tmp_path / "daily_runs" / "cache" / "market_snapshot.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps({"rows": [{"code": "430001"}, {"code": "600000"}, {"code": "830001"}]}),
        encoding="utf-8",
    )

    assert fast_t1_scan.load_bse_codes(tmp_path) == ["430001", "830001"]


def _sina_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "603986",
                "name": "兆易创新",
                "price": 395.0,
                "change_pct": 1.2,
                "volume": 1000,
                "amount": 2000,
                "ticktime": "10:47:03",
            }
        ]
    )


def _bj_snapshot(codes: list[str]) -> tuple[pd.DataFrame, list[str]]:
    return (
        pd.DataFrame(
            [
                {
                    "code": "920001",
                    "name": "北交所样例",
                    "price": 10.0,
                    "change_pct": 0.5,
                    "volume": 500,
                    "amount": 1000,
                    "quote_time": "2026-08-27 10:47:05",
                    "observed_at": "2026-08-27 10:47:06",
                    "source": "tencent_bse",
                }
            ]
        ),
        [],
    )


def test_query_realtime_quotes_merges_markets_in_input_order():
    result = fast_t1_scan.query_realtime_quotes(
        ["920001", "603986"], shsz_fetcher=_sina_snapshot, bj_fetcher=_bj_snapshot
    )
    assert result["code"].tolist() == ["920001", "603986"]
    assert result["market"].tolist() == ["bj", "sh"]
    assert (result["realtime_status"] == "已获取").all()
    # 沪深行无 quote_time：用 ticktime + 当日日期补齐（需求 7.1）
    sh_row = result.loc[result["code"] == "603986"].iloc[0]
    assert sh_row["quote_time"].endswith("10:47:03")
    assert str(sh_row["observed_at"]).strip() != ""
    # 北交所行保留自身行情时间
    assert result.loc[result["code"] == "920001", "quote_time"].iat[0] == "2026-08-27 10:47:05"


def test_query_realtime_quotes_bj_failure_does_not_hide_shsz():
    def failing_bj(codes: list[str]) -> tuple[pd.DataFrame, list[str]]:
        raise ConnectionError("bj link down")

    result = fast_t1_scan.query_realtime_quotes(
        ["603986", "920001"], shsz_fetcher=_sina_snapshot, bj_fetcher=failing_bj
    )
    assert result["code"].tolist() == ["603986", "920001"]
    assert result.loc[result["code"] == "603986", "realtime_status"].iat[0] == "已获取"
    assert result.loc[result["code"] == "920001", "realtime_status"].iat[0] == "获取失败"

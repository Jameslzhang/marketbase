from __future__ import annotations

import json
import sys
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


def test_main_writes_candidate_union_with_scan_metadata(tmp_path: Path, monkeypatch):
    fixed_now = fast_t1_scan.datetime(2026, 9, 3, 13, 45, 0, tzinfo=fast_t1_scan.CN_TZ)
    html_path = tmp_path / "report.html"
    observed_label = fixed_now.strftime("%Y-%m-%d %H:%M:%S")
    captured: dict[str, object] = {}

    class FixedDateTime(fast_t1_scan.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    def fake_snapshot(data_root: Path, observed_at, fresh_minutes: int):
        return (
            pd.DataFrame(
                [
                    {
                        "code": "600000",
                        "name": "浦发银行",
                        "market": "sh",
                        "price": 52.0,
                        "change_pct": 2.0,
                        "volume": 1_000_000,
                        "amount": 80_000_000,
                        "circ_mv": 4_000_000_000,
                        "turnover_rate": 1.2,
                        "industry": "银行",
                        "concepts": "金融",
                        "observed_at": observed_label,
                        "is_st": False,
                        "is_suspended": False,
                        "delist_risk": False,
                        "listed_days": 300,
                    }
                ]
            ),
            "fixture-snapshot",
            0.1,
            {},
        )

    def fake_indicators(df, daily_root: Path, today_str: str, workers: int, fast_dir: Path):
        return pd.DataFrame(
            [
                {
                    "code": "600000",
                    "avg5d": 400_000.0,
                    "ma5": 50.0,
                    "ma10": 49.0,
                    "ma20": 48.0,
                    "ma60": 47.0,
                    "rsi14": 55.0,
                    "atr14": 1.0,
                    "atr14_pct": 2.0,
                    "boll_upper": 55.0,
                    "boll_middle": 52.0,
                    "boll_lower": 50.0,
                    "boll_position": 0.5,
                    "return_5d": 0.03,
                    "return_10d": 0.04,
                    "return_20d": 0.05,
                    "upper_shadow_ratio": 0.1,
                    "lower_shadow_ratio": 0.1,
                    "input_rows": 250,
                }
            ]
        )

    def fake_apply_volume_ratio(df, avg5d, observed_at):
        result = df.copy()
        result["volume_ratio"] = 2.0
        result["elapsed_trade_minutes"] = 135
        return result

    def fake_build_candidate_union(frame, *, trade_date, observed_at, market_rows, funnel):
        captured["frame"] = frame.copy()
        captured["trade_date"] = trade_date
        captured["observed_at"] = observed_at
        captured["market_rows"] = market_rows
        captured["funnel"] = dict(funnel)
        return {"trade_date": trade_date, "observed_at": observed_at, "candidates": frame.to_dict("records")}

    def fake_write_candidate_union(payload, path: Path):
        captured["payload"] = payload
        captured["path"] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    monkeypatch.setattr(fast_t1_scan, "datetime", FixedDateTime)
    monkeypatch.setattr(fast_t1_scan, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(fast_t1_scan, "ensure_industry", lambda df, data_root: (df, "fixture-industry"))
    monkeypatch.setattr(fast_t1_scan, "load_or_compute_indicators", fake_indicators)
    monkeypatch.setattr(fast_t1_scan, "apply_volume_ratio", fake_apply_volume_ratio)
    monkeypatch.setattr(fast_t1_scan, "official_daily_cache_root", lambda data_root: tmp_path / "daily")
    monkeypatch.setattr(fast_t1_scan, "render_html", lambda ctx: "<html></html>")
    monkeypatch.setattr(fast_t1_scan, "build_candidate_union", fake_build_candidate_union, raising=False)
    monkeypatch.setattr(fast_t1_scan, "write_candidate_union", fake_write_candidate_union, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fast_t1_scan.py",
            "--data-root",
            str(tmp_path),
            "--fresh",
            "0",
            "--html",
            str(html_path),
        ],
    )

    rc = fast_t1_scan.main()

    assert rc == 0
    assert captured["trade_date"] == "2026-09-03"
    assert captured["observed_at"] == "2026-09-03T13:45:00+08:00"
    assert captured["market_rows"] == 1
    assert captured["funnel"] == {
        "initial": 1,
        "after_hard": 1,
        "candidates": 1,
        "official": 1,
        "watch": 0,
    }
    assert captured["path"] == tmp_path / "cache" / "fast" / "candidate_union_20260903_1345.json"
    assert len(captured["frame"]) == 1
    assert {"ma10", "ma20", "return_10d", "elapsed_trade_minutes"}.issubset(captured["frame"].columns)

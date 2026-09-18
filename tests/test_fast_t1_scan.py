from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

import fast_t1_scan


def test_targets_use_nearest_observed_resistance_not_atr_extension():
    zones = fast_t1_scan.calc_zones(pd.Series({
        "price": 100., "atr14": 4., "boll_lower": 80.,
        "boll_upper": 120., "high": 102., "high_20d": 108.,
    }))
    assert zones.sell1_low == zones.sell1_high == 102.
    assert zones.sell2_low == zones.sell2_high == 108.


def test_missing_overhead_resistance_does_not_invent_profit_target():
    zones = fast_t1_scan.calc_zones(pd.Series({
        "price": 100., "atr14": 4., "boll_lower": 80., "boll_upper": 95.,
    }))
    assert pd.isna(zones.sell1_low)
    assert pd.isna(zones.sell2_low)



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


def test_fill_missing_market_values_from_same_round_snapshot(tmp_path: Path):
    snapshot_dir = tmp_path / "daily_runs" / "2026-09-18" / "105718_intraday"
    snapshot_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "code": "603306",
            "price": 80.0,
            "circ_mv": 8_000_000_000,
            "total_mv": 10_000_000_000,
            "observed_at": "2026-09-18T10:57:18+08:00",
        }
    ]).to_csv(snapshot_dir / "market_snapshot.csv", index=False)
    newer_dir = tmp_path / "daily_runs" / "2026-09-18" / "105800_intraday"
    newer_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "code": "603306",
            "price": 81.0,
            "circ_mv": None,
            "total_mv": 10_125_000_000,
            "observed_at": "2026-09-18T10:58:00+08:00",
        }
    ]).to_csv(newer_dir / "market_snapshot.csv", index=False)
    live = pd.DataFrame([
        {"code": "603306", "price": 82.0, "circ_mv": None, "total_mv": None}
    ])

    result, source = fast_t1_scan.fill_missing_market_values(
        live,
        tmp_path,
        fast_t1_scan.datetime(2026, 9, 18, 10, 58, 30, tzinfo=fast_t1_scan.CN_TZ),
    )

    assert result.loc[0, "circ_mv"] == pytest.approx(8_200_000_000)
    assert result.loc[0, "total_mv"] == pytest.approx(10_250_000_000)
    assert result.loc[0, "market_value_source"] == "same_round_snapshot_scaled"
    assert "105718_intraday" in source


def test_indicator_cache_reuses_same_trading_day_results(tmp_path: Path, monkeypatch):
    universe = pd.DataFrame({"code": ["000001", "600000"]})
    calls = []

    def compute(frame, daily_root, today_str, workers):
        calls.append(frame["code"].tolist())
        return pd.DataFrame(
            [
                {"code": "000001", "ma5": 1.0, "avg5d": 10.0},
                {"code": "600000", "ma5": 2.0, "avg5d": 20.0},
            ]
        )

    monkeypatch.setattr(fast_t1_scan, "compute_indicators_parallel", compute)

    first = fast_t1_scan.load_or_compute_indicators(
        universe, tmp_path / "daily", "2026-09-01", 4, tmp_path / "fast"
    )
    second = fast_t1_scan.load_or_compute_indicators(
        universe, tmp_path / "daily", "2026-09-01", 4, tmp_path / "fast"
    )

    assert calls == [["000001", "600000"]]
    assert first["code"].tolist() == ["000001", "600000"]
    assert second["ma5"].tolist() == [1.0, 2.0]
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "000001.json").write_text("corrected source")
    fast_t1_scan.load_or_compute_indicators(universe, daily, "2026-09-01", 4, tmp_path / "fast")
    assert calls[-1] == ["000001"]


def test_indicator_cache_only_computes_codes_missing_from_cache(tmp_path: Path, monkeypatch):
    universe = pd.DataFrame({"code": ["000001", "600000"]})
    fast_dir = tmp_path / "fast"
    fast_dir.mkdir()
    pd.DataFrame([{"code": "000001", "ma5": 1.0, "avg5d": 10.0}]).to_csv(
        fast_dir / "indicators_2026-09-01.csv", index=False
    )
    calls = []

    def compute(frame, daily_root, today_str, workers):
        calls.append(frame["code"].tolist())
        return pd.DataFrame([{"code": code, "ma5": 2.0, "avg5d": 20.0} for code in frame["code"]])

    monkeypatch.setattr(fast_t1_scan, "compute_indicators_parallel", compute)

    result = fast_t1_scan.load_or_compute_indicators(
        universe, tmp_path / "daily", "2026-09-01", 4, fast_dir
    )

    # A legacy CSV without source provenance must not be trusted.
    assert calls == [["000001", "600000"]]
    assert result["code"].tolist() == ["000001", "600000"]


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


@pytest.mark.parametrize("empty_reason", [None, "liquidity", "channels", "circ_mv_missing"])
@pytest.mark.parametrize("explicit_output", [False, True])
def test_main_writes_candidate_union_with_scan_metadata(tmp_path: Path, monkeypatch, empty_reason, explicit_output):
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
        def row(code: str, price: float):
            return {
                "code": code,
                "name": f"测试{code}",
                "market": "sh" if code.startswith("6") else "sz",
                "price": price,
                "change_pct": 2.0,
                "volume": 1_000_000,
                "amount": 0 if empty_reason == "liquidity" else 80_000_000,
                "circ_mv": None if empty_reason == "circ_mv_missing" else 4_000_000_000,
                "turnover_rate": 1.2,
                "industry": "银行",
                "concepts": "金融",
                "observed_at": observed_label,
                "is_st": False,
                "is_suspended": False,
                "delist_risk": False,
                "listed_days": 300,
            }
        return (
            pd.DataFrame(
                [
                    row("600039", 39.99),
                    row("600040", 40.0),
                    row("600049", 49.99),
                    row("600050", 50.0),
                ]
            ),
            "fixture-snapshot",
            0.1,
            {},
        )

    def fake_indicator_frame(codes=("600040", "600049", "600050")):
        return pd.DataFrame(
            [
                    {
                        "code": code,
                        "avg5d": 400_000.0,
                        "ma5": 38.0,
                        "ma10": 37.0,
                        "ma11": 36.8,
                        "ma20": 36.0,
                        "ma23": 35.8,
                        "ma60": 35.0,
                    "rsi14": 55.0,
                    "atr14": 1.0,
                    "atr14_pct": 2.0,
                    "boll_upper": 55.0,
                    "boll_middle": 52.0,
                        "boll_lower": 35.0,
                    "boll_position": 0.5,
                    "return_5d": 0.03,
                    "return_10d": 0.04,
                        "return_20d": 0.05,
                        "momentum_delta_1": 0.02,
                        "momentum_delta_3": 0.05,
                        "repeated_upper_shadow": False,
                    "upper_shadow_ratio": 0.1,
                    "lower_shadow_ratio": 0.1,
                    "input_rows": 250,
                }
                for code in codes
            ]
        )

    def fake_compute_indicators(df, daily_root: Path, today_str: str, workers: int):
        return fake_indicator_frame(tuple(df["code"]))

    def fake_load_or_compute_indicators(
        df, daily_root: Path, today_str: str, workers: int, fast_dir: Path
    ):
        return fake_indicator_frame(tuple(df["code"]))

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
    if empty_reason == "channels":
        monkeypatch.setattr(fast_t1_scan, "research_channels", lambda row: [])
    monkeypatch.setattr(fast_t1_scan, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(fast_t1_scan, "ensure_industry", lambda df, data_root: (df, "fixture-industry"))
    monkeypatch.setattr(fast_t1_scan, "compute_indicators_parallel", fake_compute_indicators)
    if hasattr(fast_t1_scan, "load_or_compute_indicators"):
        monkeypatch.setattr(
            fast_t1_scan,
            "load_or_compute_indicators",
            fake_load_or_compute_indicators,
        )
    monkeypatch.setattr(fast_t1_scan, "apply_volume_ratio", fake_apply_volume_ratio)
    monkeypatch.setattr(fast_t1_scan, "official_daily_cache_root", lambda data_root: tmp_path / "daily")
    monkeypatch.setattr(fast_t1_scan, "render_html", lambda ctx: "<html></html>")
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
        ] + (["--candidate-output", str(tmp_path / "fixed_union.json")] if explicit_output else []),
    )

    rc = fast_t1_scan.main()

    assert rc == 0
    assert captured["payload"]["trade_date"] == "2026-09-03"
    assert captured["payload"]["observed_at"] == "2026-09-03T13:45:00+08:00"
    assert captured["payload"]["market_rows"] == 4
    if empty_reason in {"liquidity", "channels"}:
        assert captured["payload"]["candidates"] == []
        assert captured["payload"]["funnel"]["candidates"] == 0
        assert html_path.exists()
        return
    assert captured["payload"]["funnel"] == {
        "initial": 4,
        "after_hard": 3,
        "candidates": 3,
        "official": 1,
        "watch": 0,
    }
    assert captured["path"] == (tmp_path / "fixed_union.json" if explicit_output else tmp_path / "cache" / "fast" / "candidate_union_20260903_1345.json")
    by_code = {row["code"]: row for row in captured["payload"]["candidates"]}
    assert "600039" not in by_code
    assert by_code["600040"]["price_band"] == "shadow_40_50"
    assert by_code["600049"]["price_band"] == "shadow_40_50"
    assert by_code["600050"]["price_band"] == "production"
    if empty_reason == "circ_mv_missing":
        assert all(row["circ_mv_missing"] is True for row in by_code.values())
    lifecycle_fields = {
        "ma11",
        "ma23",
        "momentum_delta_1",
        "momentum_delta_3",
        "repeated_upper_shadow",
        "rps20",
    }
    assert all(lifecycle_fields.issubset(row) for row in by_code.values())


def test_rps20_uses_full_tradable_main_board_cross_section_before_candidate_filters():
    full_market_indicators = pd.DataFrame(
        [
            {"code": "000001", "return_20d": 0.10},
            {"code": "600001", "return_20d": 0.20},
            {"code": "600002", "return_20d": 0.30},
            {"code": "600003", "return_20d": 0.40},
        ]
    )
    candidate = full_market_indicators.iloc[[1]].copy()

    mapped = fast_t1_scan.map_full_market_rps20(candidate, full_market_indicators)
    candidate_only_rank = fast_t1_scan.compute_rps20(candidate).loc["600001"]

    assert mapped.iloc[0]["rps20"] == 50.0
    assert candidate_only_rank == 100.0


def test_rps20_universe_keeps_low_price_and_low_liquidity_but_excludes_zero_volume():
    eligible_main_board = pd.DataFrame(
        [
            {"code": "600001", "price": 20.0, "amount": 1_000_000, "volume": 10_000},
            {"code": "600002", "price": 80.0, "amount": 90_000_000, "volume": 20_000},
            {"code": "600003", "price": 90.0, "amount": 90_000_000, "volume": 0},
        ]
    )

    universe = fast_t1_scan.select_rps20_universe(eligible_main_board)

    assert universe["code"].tolist() == ["600001", "600002"]

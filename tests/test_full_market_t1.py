from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import pandas as pd
import pytest

import strategies.full_market_t1 as full_market_t1
from strategies.full_market_t1 import (
    CandidateObjectiveData,
    build_candidate_union,
    build_minute_evidence,
    orchestrate_full_market_t1,
    candidate_data_status,
    classify_price_band,
    compute_execution_score,
    write_candidate_union,
)


TZ_SHANGHAI = timezone(timedelta(hours=8))
SAVED_REPLAY_TRADE_DATE = "2026-09-03"
SAVED_REPLAY_OBSERVED_AT = "2026-09-03T14:41:00+08:00"
SAVED_REPLAY_GENERATED_AT = "2026-09-03T14:43:09.708426+08:00"
SAVED_REPLAY_MARKET_ROWS = 5546
SAVED_REPLAY_RUN_NAME = "144309_intraday_1430_objective_data"
SAVED_REPLAY_SCAN_NAME = "scan_result_20260903_1441.csv"
SAVED_REPLAY_SOURCE_BLOCK = re.compile(
    r"<!-- task6-source-records:start -->\s*```json\s*(.*?)\s*```\s*<!-- task6-source-records:end -->",
    re.DOTALL,
)


def _saved_replay_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _saved_replay_fixture_dir() -> Path:
    return _saved_replay_repo_root() / "tests" / "fixtures" / "full_market_t1" / SAVED_REPLAY_TRADE_DATE


def _saved_replay_expected_summary() -> dict[str, object]:
    return json.loads((_saved_replay_fixture_dir() / "expected_summary.json").read_text(encoding="utf-8"))


def _saved_replay_manifest_key() -> str:
    return f"data/daily_runs/{SAVED_REPLAY_TRADE_DATE}/{SAVED_REPLAY_RUN_NAME}/manifest.json"


def _saved_replay_data_audit_key() -> str:
    return f"data/daily_runs/{SAVED_REPLAY_TRADE_DATE}/{SAVED_REPLAY_RUN_NAME}/data_audit.json"


def _saved_replay_source_paths(repo_root: Path) -> dict[str, Path]:
    run_dir = repo_root / "data" / "daily_runs" / SAVED_REPLAY_TRADE_DATE / SAVED_REPLAY_RUN_NAME
    return {
        f"data/cache/fast/{SAVED_REPLAY_SCAN_NAME}": repo_root / "data" / "cache" / "fast" / SAVED_REPLAY_SCAN_NAME,
        **{
            f"data/daily_runs/{SAVED_REPLAY_TRADE_DATE}/{SAVED_REPLAY_RUN_NAME}/{name}": run_dir / name
            for name in (
                "market_snapshot.json", "daily_indicators.csv", "classification_map.csv",
                "industry_agg.csv", "market_breadth.json", "intraday_minutes.parquet",
                "data_audit.json", "manifest.json",
            )
        },
    }


def _load_saved_replay_source_records() -> list[dict[str, object]]:
    readme_path = _saved_replay_fixture_dir() / "README.md"
    match = SAVED_REPLAY_SOURCE_BLOCK.search(readme_path.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("Task6 README is missing the source-records JSON block")
    records = json.loads(match.group(1))
    if not isinstance(records, list) or not records:
        raise AssertionError("Task6 README source-records block must be a non-empty list")
    return records


def _require_saved_replay_sources(repo_root: Path) -> dict[str, Path]:
    source_paths = _saved_replay_source_paths(repo_root)
    missing = [relative for relative, path in source_paths.items() if not path.is_file()]
    if missing:
        pytest.skip(f"saved full-market T1 replay sources are unavailable: {', '.join(missing)}")
    return source_paths


def _verify_saved_replay_source_hashes(repo_root: Path) -> dict[str, Path]:
    source_paths = _require_saved_replay_sources(repo_root)
    expected_records = _load_saved_replay_source_records()
    expected_by_path = {
        str(record["path"]): record
        for record in expected_records
        if isinstance(record, dict) and isinstance(record.get("path"), str)
    }
    assert set(expected_by_path) == set(source_paths)
    for relative, path in source_paths.items():
        payload = path.read_bytes()
        record = expected_by_path[relative]
        assert record["bytes"] == len(payload)
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    return source_paths


def _saved_replay_manifest(source_paths: dict[str, Path]) -> dict[str, object]:
    return json.loads(source_paths[_saved_replay_manifest_key()].read_text(encoding="utf-8"))


def _saved_replay_data_audit(source_paths: dict[str, Path]) -> dict[str, object]:
    return json.loads(source_paths[_saved_replay_data_audit_key()].read_text(encoding="utf-8"))


def _replay_saved_full_market_t1_inputs(tmp_path: Path) -> tuple[dict[str, object], dict[str, Path]]:
    repo_root = _saved_replay_repo_root()
    source_paths = _verify_saved_replay_source_hashes(repo_root)
    saved_manifest = _saved_replay_manifest(source_paths)
    frame = pd.read_csv(source_paths[f"data/cache/fast/{SAVED_REPLAY_SCAN_NAME}"], dtype={"code": str})
    daily = pd.read_csv(
        source_paths[f"data/daily_runs/{SAVED_REPLAY_TRADE_DATE}/{SAVED_REPLAY_RUN_NAME}/daily_indicators.csv"],
        dtype={"code": str},
    )
    lifecycle_fields = [
        "code", "ma11", "ma23", "momentum_delta_1", "momentum_delta_3",
        "repeated_upper_shadow", "rps20", "atr14",
    ]
    # Equivalent to the current scanner artifact: enrich the saved scan with the exact
    # same-run lifecycle indicator contract instead of substituting approximate fields.
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame = frame.drop(columns=[field for field in lifecycle_fields[1:] if field in frame.columns]).merge(
        daily[lifecycle_fields], on="code", how="left"
    )
    candidate_union = build_candidate_union(
        frame,
        trade_date=SAVED_REPLAY_TRADE_DATE,
        observed_at=SAVED_REPLAY_OBSERVED_AT,
        market_rows=SAVED_REPLAY_MARKET_ROWS,
        funnel={
            "initial": SAVED_REPLAY_MARKET_ROWS,
            "fast_scan_candidates": len(frame),
            "candidates": len(frame),
        },
    )
    candidate_union_path = tmp_path / "candidate_union.json"
    candidate_union_path.write_text(json.dumps(candidate_union, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_payload = {
        "schema_version": 1,
        "generated_at": saved_manifest["generated_at"],
        **{
            key: str(source_paths[f"data/daily_runs/{SAVED_REPLAY_TRADE_DATE}/{SAVED_REPLAY_RUN_NAME}/{name}"].resolve())
            for key, name in {
                "market_snapshot_path": "market_snapshot.json",
                "daily_indicators_path": "daily_indicators.csv",
                "classification_map_path": "classification_map.csv",
                "industry_agg_path": "industry_agg.csv",
                "market_breadth_path": "market_breadth.json",
                "intraday_minutes_path": "intraday_minutes.parquet",
                "data_audit_path": "data_audit.json",
            }.items()
        },
    }
    (tmp_path / "latest_codex_input.json").write_text(
        json.dumps(latest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    decision = orchestrate_full_market_t1(
        data_root=tmp_path,
        candidate_union_path=candidate_union_path,
        decision_at=datetime(2026, 9, 3, 14, 43, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )
    return decision, source_paths


def _assert_reason_codes_present(rows: list[dict[str, object]]) -> None:
    for row in rows:
        assert row["reason_codes"], f"{row['code']} missing reason codes for {row['decision']}"


def _base_snapshot(**overrides):
    payload = {
        "code": "1234",
        "price": 10.0,
        "change_pct": 3.2,
        "turnover_rate": 4.8,
        "low": 9.7,
        "high": 10.8,
        "long_upper_shadow": False,
        "repeated_upper_shadow": False,
        "limit_proximity": False,
    }
    payload.update(overrides)
    return payload


def _base_daily(**overrides):
    payload = {
        "ma5": 9.9,
        "ma10": 9.8,
        "ma11": 9.8,
        "ma20": 9.7,
        "ma23": 9.6,
        "rsi14": 55.0,
        "rps20": 80.0,
        "momentum_delta_1": 0.02,
        "momentum_delta_3": 0.05,
        "boll_position": 0.5,
        "return_5d": 0.03,
        "return_20d": 0.09,
        "atr14": 1.0,
        # compute_daily_indicators contract: percentage points (2.0 means 2%).
        "atr14_pct": 2.0,
    }
    payload.update(overrides)
    return payload


def _base_industry(**overrides):
    payload = {"industry": "bank", "advance_ratio": 0.62, "industry_sync": True}
    payload.update(overrides)
    return payload


def _base_minute(**overrides):
    payload = {
        "vwap": 10.05,
        "afternoon_vwap": 10.08,
        "named_pivot": 10.02,
    }
    payload.update(overrides)
    return payload


def _base_executability(**overrides):
    payload = {
        "dist_vwap_pct": 3.0,
        "change_pct": 9.0,
        "turnover_rate": 12.0,
        "from_low_pct": 8.0,
        "dist_high_pct": -4.0,
        "amplitude_pct": 9.0,
        "buy_low": 50.0,
        "buy_high": 55.0,
        "no_chase_price": 54.5,
        "protection_constructible": True,
        "is_untradable": False,
        "fee_adjusted_rr": 1.8,
    }
    payload.update(overrides)
    return payload


def _candidate(**overrides):
    payload = {
        "code": "600000",
        "name": "浦发银行",
        "market": "sh",
        "price": 52.0,
        "amount": 80_000_000,
        "opportunity_score": 70.0,
        "price_band": "production",
        "candidate_reason": ["trend_full"],
        "trend_aligned": True,
        "volume_ratio": 2.0,
        "tail_risk_tags": [],
        "sell1_low": 57.0,
    }
    payload.update(overrides)
    return payload


def _objective(
    *,
    snapshot_overrides=None,
    daily_overrides=None,
    industry_overrides=None,
    minute_overrides=None,
    minute_evidence_overrides=None,
    executability_overrides=None,
):
    minute = _base_minute(**(minute_overrides or {}))
    minute_evidence = {
        "confirmed": True,
        "vwap": minute["vwap"],
        "afternoon_vwap": minute["afternoon_vwap"],
        "named_pivot": minute["named_pivot"],
        "reason_codes": [],
    }
    minute_evidence.update(minute_evidence_overrides or {})
    return CandidateObjectiveData(
        snapshot=_base_snapshot(**(snapshot_overrides or {})),
        daily=_base_daily(**(daily_overrides or {})),
        industry=_base_industry(**(industry_overrides or {})),
        minute=minute,
        minute_evidence=minute_evidence,
        executability=_base_executability(**(executability_overrides or {})),
    )


def _market(**overrides):
    payload = {
        "trade_date": "2026-09-03",
        "advance_ratio": 0.62,
        "quality_status": "ready",
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def _handoff(candidates, objectives, *, market=None):
    return {
        "schema_version": "1.0.0",
        "trade_date": "2026-09-03",
        "observed_at": "2026-09-03T13:45:00+08:00",
        "market_rows": 5546,
        "funnel": {"initial": 5546, "candidates": len(candidates)},
        "candidates": candidates,
        "objective_by_code": objectives,
        "market": market or _market(),
    }


def _minute_rows():
    return pd.DataFrame(
        [
            {"time": "13:39", "close": 10.00, "volume": 100.0, "amount": 1000.0, "named_pivot": 10.02},
            {"time": "13:40", "close": 10.01, "volume": 100.0, "amount": 1001.0, "named_pivot": 10.02},
            {"time": "13:41", "close": 10.02, "volume": 100.0, "amount": 1002.0, "named_pivot": 10.02},
            {"time": "13:42", "close": 10.11, "volume": 90.0, "amount": 909.9, "named_pivot": 10.02},
            {"time": "13:43", "close": 10.13, "volume": 85.0, "amount": 861.05, "named_pivot": 10.02},
            {"time": "13:44", "close": 10.15, "volume": 85.0, "amount": 862.75, "named_pivot": 10.02},
            {"time": "13:45", "close": 9.80, "volume": 500.0, "amount": 4900.0, "named_pivot": 10.50},
        ]
    )


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (39.99, "excluded"),
        (40.00, "shadow_40_50"),
        (49.99, "shadow_40_50"),
        (50.00, "production"),
    ],
)
def test_classify_price_band_boundaries(price, expected):
    assert classify_price_band("600000", "sh", price) == expected


def test_non_main_board_is_excluded_at_any_price():
    assert classify_price_band("300001", "sz", 80.0) == "excluded"
    assert classify_price_band("301001", "sz", 80.0) == "excluded"
    assert classify_price_band("688001", "sh", 80.0) == "excluded"
    assert classify_price_band("920001", "bj", 80.0) == "excluded"


def test_build_candidate_union_preserves_metadata_and_normalizes_reasons():
    frame = pd.DataFrame(
        [
            {
                "code": "600000",
                "market": "sh",
                "name": "浦发银行",
                "price": 52.0,
                "amount": 80_000_000,
                "atr14_pct": 2.0,
                "opportunity_tags": "trend_full|vr_good(2.0)",
                "buy_low": 51.2,
                "buy_high": 51.8,
                "chase_line": 52.1,
                "protect": 50.8,
                "sell1_low": 54.4,
            },
            {
                "code": "000001",
                "market": "sz",
                "name": "平安银行",
                "price": 45.0,
                "amount": 60_000_000,
                "opportunity_tags": ["shadow_watch"],
                "buy_low": 44.2,
                "buy_high": 44.7,
                "chase_line": 45.0,
                "protect": 44.0,
                "sell1_low": 46.3,
            },
            {
                "code": "600519",
                "market": "sh",
                "name": "贵州茅台",
                "price": 1800.0,
                "amount": 90_000_000,
                "buy_low": 1785.0,
                "buy_high": 1790.0,
                "chase_line": 1795.0,
                "protect": 1770.0,
                "sell1_low": 1830.0,
            },
        ]
    )

    payload = build_candidate_union(
        frame,
        trade_date="2026-09-03",
        observed_at="2026-09-03T13:45:00+08:00",
        market_rows=5546,
        funnel={"initial": 5546, "after_hard": 812, "candidates": 37, "official": 3},
    )

    assert payload["schema_version"] == "1.0.0"
    assert payload["trade_date"] == "2026-09-03"
    assert payload["observed_at"] == "2026-09-03T13:45:00+08:00"
    assert payload["market_rows"] == 5546
    assert payload["funnel"] == {
        "initial": 5546,
        "after_hard": 812,
        "candidates": 37,
        "official": 3,
    }
    assert [row["code"] for row in payload["candidates"]] == ["600000", "000001", "600519"]
    assert payload["candidates"][0]["candidate_reason"] == ["trend_full", "vr_good(2.0)"]
    assert payload["candidates"][0]["price_band"] == "production"
    assert payload["candidates"][0]["protection_constructible"] is True
    assert payload["candidates"][0]["protection_constructible_source"] == "candidate_guardrail_formula_v1"
    assert payload["candidates"][0]["fee_adjusted_rr_formula_version"] == "cn_equity_fee_v1"
    assert payload["candidates"][0]["fee_adjusted_rr_source"] == "candidate_cn_equity_fee_formula_v1"
    assert payload["candidates"][0]["fee_adjusted_rr"] == pytest.approx(2.4868)
    assert payload["candidates"][0]["atr14_pct_unit"] == "percent_points"
    assert payload["candidates"][1]["candidate_reason"] == ["shadow_watch"]
    assert payload["candidates"][1]["price_band"] == "shadow_40_50"
    assert payload["candidates"][2]["candidate_reason"] == []
    assert payload["candidates"][2]["amount"] == 90_000_000


def test_candidate_union_is_never_buyable_at_generation_time():
    frame = pd.DataFrame(
        [
            {"code": "600000", "market": "sh", "price": 52.0, "opportunity_tags": ["trend_full"]},
            {"code": "000001", "market": "sz", "price": 45.0, "opportunity_tags": ["shadow_watch"]},
        ]
    )

    payload = build_candidate_union(
        frame,
        trade_date="2026-09-03",
        observed_at="2026-09-03T13:45:00+08:00",
        market_rows=5546,
        funnel={"initial": 5546},
    )

    assert all(row["production_buyable"] is False for row in payload["candidates"])
    assert all(row["buyable"] is False for row in payload["candidates"])
    assert all(row["only_choose_one_eligible"] is False for row in payload["candidates"])


def test_build_candidate_union_preserves_explicit_executability_fields_without_aliasing_rr_ratio():
    frame = pd.DataFrame(
        [
            {
                "code": "600000",
                "market": "sh",
                "price": 52.0,
                "buy_high": 51.5,
                "protect": 50.9,
                "sell1_low": 53.7,
                "protection_constructible": False,
                "fee_adjusted_rr": 1.61,
                "fee_adjusted_rr_source": "upstream_explicit",
                "fee_adjusted_rr_formula_version": "upstream_v9",
                "rr_ratio": 9.99,
            },
            {
                "code": "600001",
                "market": "sh",
                "price": 52.0,
                "buy_high": 51.5,
                "protect": 52.5,
                "sell1_low": 53.7,
                "rr_ratio": 1.88,
            },
            {
                "code": "600002",
                "market": "sh",
                "price": 52.0,
                "buy_low": 51.0,
                "buy_high": 51.5,
                "chase_line": 51.8,
                "protect": 50.9,
                "sell1_low": 53.7,
            },
        ]
    )

    payload = build_candidate_union(
        frame,
        trade_date="2026-09-03",
        observed_at="2026-09-03T13:45:00+08:00",
        market_rows=3,
        funnel={"initial": 3},
    )

    first, second, third = payload["candidates"]
    assert first["protection_constructible"] is False
    assert first["protection_constructible_source"] == "explicit_candidate_field"
    assert first["fee_adjusted_rr"] == 1.61
    assert first["fee_adjusted_rr_source"] == "upstream_explicit"
    assert first["fee_adjusted_rr_formula_version"] == "upstream_v9"
    assert second["protection_constructible"] is None
    assert second["protection_constructible_source"] == "missing"
    assert second["fee_adjusted_rr"] is None
    assert second["fee_adjusted_rr_source"] == "missing"
    assert second["fee_adjusted_rr_formula_version"] == "cn_equity_fee_v1"
    assert third["protection_constructible"] is True
    assert third["protection_constructible_source"] == "candidate_guardrail_formula_v1"
    assert third["fee_adjusted_rr"] == pytest.approx(3.4591)
    assert third["fee_adjusted_rr_source"] == "candidate_cn_equity_fee_formula_v1"
    assert third["fee_adjusted_rr_formula_version"] == "cn_equity_fee_v1"


def test_write_candidate_union_writes_utf8_json_atomically(tmp_path: Path, monkeypatch):
    target = tmp_path / "candidate_union.json"
    target.write_text('{"old": true}', encoding="utf-8")
    payload = {
        "schema_version": "1.0.0",
        "trade_date": "2026-09-03",
        "observed_at": "2026-09-03T13:45:00+08:00",
        "market_rows": 1,
        "funnel": {"initial": 1},
        "candidates": [{"code": "600000", "name": "浦发银行"}],
    }
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        src_path = Path(source)
        dst_path = Path(destination)
        replace_calls.append((src_path, dst_path))
        assert src_path.exists()
        original_replace(src_path, dst_path)

    monkeypatch.setattr("strategies.full_market_t1.os.replace", record_replace)

    result = write_candidate_union(payload, target)

    assert result == target
    assert replace_calls == [(target.with_suffix(".tmp"), target)]
    assert not target.with_suffix(".tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_candidate_data_status_is_ready_when_required_evidence_exists():
    status, reasons = candidate_data_status(
        _base_snapshot(),
        _base_daily(),
        _base_industry(),
        _base_minute(),
    )

    assert status == "ready"
    assert reasons == []


@pytest.mark.parametrize(
    ("snapshot", "daily", "industry", "minute", "expected_reason"),
    [
        (None, _base_daily(), _base_industry(), _base_minute(), "snapshot_missing"),
        (_base_snapshot(), None, _base_industry(), _base_minute(), "daily_missing"),
        (_base_snapshot(), _base_daily(), None, _base_minute(), "industry_missing"),
        (_base_snapshot(), _base_daily(), _base_industry(), None, "minute_missing"),
        (_base_snapshot(), _base_daily(), _base_industry(), {"named_pivot": 10.02}, "vwap_missing"),
    ],
)
def test_candidate_data_status_reports_exact_missing_reason(snapshot, daily, industry, minute, expected_reason):
    status, reasons = candidate_data_status(snapshot, daily, industry, minute)

    assert status == "data_insufficient"
    assert reasons == [expected_reason]


def test_build_minute_evidence_confirms_with_completed_minutes_only():
    evidence = build_minute_evidence(
        _minute_rows(),
        code="1234",
        observed_at=datetime(2026, 9, 3, 13, 45, 30, tzinfo=TZ_SHANGHAI),
    )

    assert evidence["code"] == "001234"
    assert evidence["confirmed"] is True
    assert evidence["hold_minutes"] == ["13:42", "13:43", "13:44"]
    assert evidence["last_completed_minute"] == "13:44"
    assert evidence["activity_non_contracting"] is True
    assert evidence["reason_codes"] == []
    assert evidence["named_pivot"] == pytest.approx(10.02)
    assert evidence["vwap"] == pytest.approx(round((5636.7 / 560.0), 4))
    assert evidence["afternoon_vwap"] == pytest.approx(round((5636.7 / 560.0), 4))


def test_build_minute_evidence_excludes_current_unfinished_minute():
    evidence = build_minute_evidence(
        _minute_rows(),
        code="1234",
        observed_at=datetime(2026, 9, 3, 13, 45, 30, tzinfo=TZ_SHANGHAI),
    )

    assert evidence["last_completed_minute"] == "13:44"
    assert evidence["hold_minutes"][-1] == "13:44"
    assert "13:45" not in evidence["hold_minutes"]
    assert evidence["named_pivot"] == pytest.approx(10.02)


def test_build_minute_evidence_accepts_timestamp_column_for_completed_minute_lookup():
    evidence = build_minute_evidence(
        pd.DataFrame(
            [
                {
                    "code": "1234",
                    "timestamp": f"2026-09-03T{label}:00+08:00",
                    "close": close,
                    "volume": volume,
                    "amount": round(close * volume, 2),
                    "named_pivot": 10.02,
                }
                for label, close, volume in (
                    ("13:39", 10.00, 100.0),
                    ("13:40", 10.01, 100.0),
                    ("13:41", 10.02, 100.0),
                    ("13:42", 10.11, 90.0),
                    ("13:43", 10.13, 85.0),
                    ("13:44", 10.15, 85.0),
                    ("13:45", 9.80, 500.0),
                )
            ]
        ),
        code="1234",
        observed_at=datetime(2026, 9, 3, 13, 45, 30, tzinfo=TZ_SHANGHAI),
    )

    assert evidence["confirmed"] is True
    assert evidence["last_completed_minute"] == "13:44"
    assert evidence["hold_minutes"] == ["13:42", "13:43", "13:44"]


def test_build_minute_evidence_requires_six_completed_rows_to_confirm():
    evidence = build_minute_evidence(
        _minute_rows().iloc[:5],
        code="1234",
        observed_at=datetime(2026, 9, 3, 13, 44, 30, tzinfo=TZ_SHANGHAI),
    )

    assert evidence["confirmed"] is False
    assert evidence["reason_codes"] == ["insufficient_completed_minutes"]
    assert evidence["last_completed_minute"] == "13:43"


def test_candidate_data_status_preserves_missing_minute_reasons_from_minute_evidence():
    minute = build_minute_evidence(
        pd.DataFrame(),
        code="1234",
        observed_at=datetime(2026, 9, 3, 13, 45, 30, tzinfo=TZ_SHANGHAI),
    )

    status, reasons = candidate_data_status(
        _base_snapshot(),
        _base_daily(),
        _base_industry(),
        minute,
    )

    assert status == "data_insufficient"
    assert reasons[:2] == ["minute_missing", "vwap_missing"]
    assert reasons.count("minute_missing") == 1
    assert reasons.count("vwap_missing") == 1


def test_candidate_data_status_only_merges_data_readiness_reasons_from_minute_payload():
    status, reasons = candidate_data_status(
        _base_snapshot(),
        _base_daily(),
        _base_industry(),
        {
            "vwap": None,
            "reason_codes": [
                "activity_contracting",
                "minute_missing",
                "vwap_missing",
                "vwap_missing",
                "hold_below_vwap",
            ],
        },
    )

    assert status == "data_insufficient"
    assert reasons == ["minute_missing", "vwap_missing"]


def test_compute_execution_score_uses_versioned_formula_and_penalty():
    evidence = {
        "dist_vwap_pct": 3.0,
        "change_pct": 9.0,
        "turnover_rate": 12.0,
        "from_low_pct": 8.0,
        "dist_high_pct": -4.0,
        "amplitude_pct": 9.0,
    }

    assert compute_execution_score(evidence) == 84.0


def test_compute_execution_score_clamps_inputs_and_score():
    evidence = {
        "dist_vwap_pct": -100.0,
        "change_pct": -100.0,
        "turnover_rate": 100.0,
        "from_low_pct": 100.0,
        "dist_high_pct": 100.0,
        "amplitude_pct": 1.0,
    }

    assert compute_execution_score(evidence) == 36.0


def test_compute_execution_score_fails_closed_when_inputs_are_missing():
    with pytest.raises(ValueError, match="missing execution evidence: dist_high_pct"):
        compute_execution_score(
            {
                "dist_vwap_pct": 1.0,
                "change_pct": 2.0,
                "turnover_rate": 3.0,
                "from_low_pct": 4.0,
                "amplitude_pct": 5.0,
            }
        )


def test_candidate_objective_data_is_frozen():
    candidate = CandidateObjectiveData(
        snapshot=_base_snapshot(),
        daily=_base_daily(),
        industry=_base_industry(),
        minute=_base_minute(),
        minute_evidence={"confirmed": True},
        executability={"execution_rule_version": "1.0.0"},
    )

    with pytest.raises(FrozenInstanceError):
        candidate.snapshot = {}


def test_buyable_requires_both_scores_and_all_hard_gates():
    row = full_market_t1.evaluate_candidate(
        _candidate(opportunity_score=70.0, price_band="production"),
        _objective(),
        _market(),
    )

    assert row["execution_score"] == 84.0
    assert row["production_buyable"] is True
    assert row["buyable"] is True
    assert row["only_choose_one_eligible"] is True
    assert row["decision"] == "executable_candidate"
    assert row["reason_codes"] == []
    assert row["strategy_channel"] == "trend_recovery"
    assert row["lifecycle_channel"] == "trend_continuation"
    assert row["channel_mapping_version"] == "frozen_v2_to_lifecycle_v1"
    assert row["dual_axis"]["decision"] == "can_enter_candidate"
    assert row["dual_axis"]["tail_risk"] == "low"
    assert row["entry_state"] == "entry_active"
    assert [item["to_state"] for item in row["entry_state_trajectory"]] == [
        "deep_watch",
        "conditional_watch",
        "confirmed_candidate",
        "plan_published",
        "entry_active",
    ]


def test_frozen_v2_channel_mapping_is_explicit_and_complete():
    assert full_market_t1.LIFECYCLE_TO_FROZEN_CHANNEL == {
        "strong_pullback_reclaim": "stable_pullback",
        "trend_continuation": "trend_recovery",
        "high_momentum": "high_momentum",
        "sector_reversal_challenger": "oversold_theme_reversal",
    }


def test_global_data_not_ready_vetoes_every_production_action_but_keeps_audit_rows():
    candidates = [_candidate(code="600001"), _candidate(code="600002")]
    objectives = {candidate["code"]: _objective() for candidate in candidates}

    decision = full_market_t1.build_full_market_decision(
        _handoff(candidates, objectives),
        {
            "critical_ready": False,
            "objective_by_code": objectives,
            "market": _market(critical_ready=False),
        },
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
    )

    assert decision["global_status"] == "data_not_ready"
    assert decision["only_choose_one"] is None
    assert decision["executable"] == []
    assert decision["summary"]["executable"] == 0
    assert len(decision["audit_rows"]) == 2
    for row in decision["audit_rows"]:
        assert row["production_buyable"] is False
        assert row["buyable"] is False
        assert row["only_choose_one_eligible"] is False
        assert "global_data_not_ready" in row["reason_codes"]
        assert row["entry_state"] == "rejected"
        assert row["dual_axis"]["reason_code"] == "market_veto"


def test_handoff_not_ready_vetoes_lifecycle_even_when_market_context_is_ready():
    candidate = _candidate(code="600001")
    objective = _objective()

    decision = full_market_t1.build_full_market_decision(
        _handoff([candidate], {"600001": objective}),
        {
            "critical_ready": False,
            "objective_by_code": {"600001": objective},
            "market": _market(critical_ready=True),
        },
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
    )

    row = decision["audit_rows"][0]
    assert decision["global_status"] == "data_not_ready"
    assert decision["only_choose_one"] is None
    assert row["entry_state"] != "entry_active"
    assert row["dual_axis"]["status"] == "evaluated"
    assert row["dual_axis"]["reason_code"] == "market_veto"
    assert row["dual_axis"]["decision"] == "reject"
    assert row["production_buyable"] is False
    assert row["buyable"] is False
    assert row["only_choose_one_eligible"] is False


@pytest.mark.parametrize(
    ("field_overrides", "objective_kwargs", "market_overrides", "expected_reason", "expected_decision"),
    [
        ({"price_band": "excluded"}, {}, {}, "price_band_excluded", "reject"),
        ({"price_band": "shadow_40_50"}, {}, {}, "shadow_price_band", "shadow_watch"),
        ({"opportunity_score": 64.9}, {}, {}, "opportunity_score_below_threshold", "conditional_watch"),
        ({}, {"minute_evidence_overrides": {"confirmed": False}}, {}, "minute_confirmation_pending", "conditional_watch"),
        ({}, {"executability_overrides": {"buy_low": 52.5}}, {}, "buy_zone_not_ready", "conditional_watch"),
        ({}, {"executability_overrides": {"no_chase_price": 51.0}}, {}, "above_no_chase_price", "conditional_watch"),
        ({}, {"industry_overrides": {"industry_sync": False}}, {}, "industry_sync_pending", "conditional_watch"),
        ({}, {}, {"advance_ratio": 0.34}, "market_breadth_below_threshold", "reject"),
        ({}, {"executability_overrides": {"protection_constructible": False}}, {}, "protection_not_constructible", "conditional_watch"),
        ({}, {"executability_overrides": {"is_untradable": True}}, {}, "candidate_untradable", "reject"),
        ({}, {"executability_overrides": {"fee_adjusted_rr": 1.49}}, {}, "fee_adjusted_rr_below_threshold", "conditional_watch"),
        ({}, {"executability_overrides": {"dist_vwap_pct": None}}, {}, "execution_score_data_missing", "data_insufficient"),
    ],
)
def test_single_failed_hard_gate_vetoes_buyable(field_overrides, objective_kwargs, market_overrides, expected_reason, expected_decision):
    row = full_market_t1.evaluate_candidate(
        _candidate(**field_overrides),
        _objective(**objective_kwargs),
        _market(**market_overrides),
    )

    assert row["buyable"] is False
    assert row["production_buyable"] is False
    assert row["only_choose_one_eligible"] is False
    assert row["decision"] == expected_decision
    assert expected_reason in row["reason_codes"]


@pytest.mark.parametrize(
    ("executability_overrides", "expected_reason"),
    [
        ({"buy_low": None}, "buy_zone_missing"),
        ({"buy_high": None}, "buy_zone_missing"),
        ({"no_chase_price": None, "chase_line": None}, "no_chase_missing"),
        ({"protection_constructible": None}, "protection_constructibility_missing"),
        ({"is_untradable": None}, "executability_status_missing"),
        ({"fee_adjusted_rr": None}, "fee_adjusted_rr_missing"),
    ],
)
def test_missing_executability_fields_fail_closed(executability_overrides, expected_reason):
    base = _base_executability()
    if "no_chase_price" not in executability_overrides and expected_reason != "no_chase_missing":
        base["no_chase_price"] = 54.5
    base.update(executability_overrides)

    row = full_market_t1.evaluate_candidate(
        _candidate(),
        _objective(executability_overrides=base),
        _market(),
    )

    assert row["decision"] == "data_insufficient"
    assert row["production_buyable"] is False
    assert row["buyable"] is False
    assert row["only_choose_one_eligible"] is False
    assert expected_reason in row["reason_codes"]


def test_missing_data_produces_data_insufficient_without_exception():
    row = full_market_t1.evaluate_candidate(
        _candidate(),
        CandidateObjectiveData(
            snapshot=_base_snapshot(),
            daily=_base_daily(),
            industry=None,
            minute=None,
            minute_evidence={"confirmed": False, "reason_codes": ["minute_missing"]},
            executability=_base_executability(),
        ),
        _market(),
    )

    assert row["decision"] == "data_insufficient"
    assert row["buyable"] is False
    assert row["reason_codes"] == ["industry_missing", "minute_missing", "vwap_missing"]


@pytest.mark.parametrize(
    ("industry_value", "expected_reason", "expected_decision"),
    [
        ({"industry": "bank", "advance_ratio": 0.62}, "industry_sync_missing", "data_insufficient"),
        ({"industry_sync": None}, "industry_sync_missing", "data_insufficient"),
        ({"industry_sync": False}, "industry_sync_pending", "conditional_watch"),
    ],
)
def test_industry_sync_requires_explicit_boolean_evidence(industry_value, expected_reason, expected_decision):
    row = full_market_t1.evaluate_candidate(
        _candidate(),
        CandidateObjectiveData(
            snapshot=_base_snapshot(),
            daily=_base_daily(),
            industry=industry_value,
            minute=_base_minute(),
            minute_evidence={
                "confirmed": True,
                "vwap": 10.05,
                "afternoon_vwap": 10.08,
                "named_pivot": 10.02,
                "reason_codes": [],
            },
            executability=_base_executability(),
        ),
        _market(),
    )

    assert row["decision"] == expected_decision
    assert expected_reason in row["reason_codes"]


def test_shadow_candidate_can_never_be_selected():
    shadow = full_market_t1.evaluate_candidate(
        _candidate(code="000001", market="sz", price=45.0, amount=60_000_000, opportunity_score=90.0, price_band="shadow_40_50"),
        _objective(),
        _market(),
    )

    assert shadow["decision"] == "shadow_watch"
    assert shadow["production_buyable"] is False
    assert shadow["buyable"] is False
    assert shadow["only_choose_one_eligible"] is False
    assert full_market_t1.choose_one([shadow]) is None


def test_choose_one_uses_exact_tiebreak_order():
    rows = [
        {"code": "600001", "price_band": "production", "buyable": True, "only_choose_one_eligible": True, "execution_score": 82.0, "opportunity_score": 70.0, "fee_adjusted_rr": 1.8, "amount": 50_000_000},
        {"code": "600002", "price_band": "production", "buyable": True, "only_choose_one_eligible": True, "execution_score": 83.0, "opportunity_score": 65.0, "fee_adjusted_rr": 1.7, "amount": 40_000_000},
        {"code": "600003", "price_band": "production", "buyable": True, "only_choose_one_eligible": True, "execution_score": 83.0, "opportunity_score": 66.0, "fee_adjusted_rr": 1.7, "amount": 39_000_000},
        {"code": "600004", "price_band": "production", "buyable": True, "only_choose_one_eligible": True, "execution_score": 83.0, "opportunity_score": 66.0, "fee_adjusted_rr": 1.9, "amount": 10_000_000},
        {"code": "600005", "price_band": "production", "buyable": False, "only_choose_one_eligible": False, "execution_score": 99.0, "opportunity_score": 99.0, "fee_adjusted_rr": 9.9, "amount": 99_000_000},
    ]

    assert full_market_t1.choose_one(rows) == "600004"


def test_build_full_market_decision_groups_are_mutually_exclusive_and_executable_is_capped():
    candidates = [
        _candidate(code="600001", amount=91_000_000, opportunity_score=71.0),
        _candidate(code="600002", amount=88_000_000, opportunity_score=72.0),
        _candidate(code="600003", amount=86_000_000, opportunity_score=73.0),
        _candidate(code="600004", amount=84_000_000, opportunity_score=74.0),
        _candidate(code="600005", opportunity_score=70.0),
        _candidate(code="000001", market="sz", price=45.0, amount=60_000_000, opportunity_score=90.0, price_band="shadow_40_50"),
    ]
    objectives = {
        "600001": _objective(executability_overrides={"change_pct": 9.0}),
        "600002": _objective(executability_overrides={"change_pct": 8.5}),
        "600003": _objective(executability_overrides={"change_pct": 8.0}),
        "600004": _objective(executability_overrides={"change_pct": 7.5}),
        "600005": _objective(minute_evidence_overrides={"confirmed": False}),
        "000001": _objective(),
    }

    decision = full_market_t1.build_full_market_decision(
        _handoff(candidates, objectives, market=_market(warnings=["industry_classification_partial"])),
        {"objective_by_code": objectives, "market": _market(warnings=["industry_classification_partial"])},
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
    )

    assert decision["schema_version"] == "1.0.0"
    assert decision["global_status"] == "decision_ready"
    assert decision["only_choose_one"] == "600001"
    assert decision["summary"] == {
        "executable": 4,
        "executable_exposed": 3,
        "watch": 1,
        "rejected": 0,
        "shadow_count": 1,
        "evaluated": 6,
    }
    assert [row["code"] for row in decision["executable"]] == ["600001", "600002", "600003"]
    assert [row["code"] for row in decision["watch"]] == ["600005"]
    assert [row["code"] for row in decision["shadow"]] == ["000001"]
    assert decision["rejected"] == []
    assert len(decision["audit_rows"]) == 6
    fourth = next(row for row in decision["audit_rows"] if row["code"] == "600004")
    assert fourth["decision"] == "executable_candidate"
    assert fourth["buyable"] is True
    assert fourth["production_buyable"] is True
    assert fourth["only_choose_one_eligible"] is True


def _minute_rows_for(code: str, *, pivot: float, closes: list[float] | None = None) -> list[dict[str, object]]:
    completed_closes = closes or [52.05, 52.1, 52.12, 52.3, 52.35, 52.4]
    labels = ["13:39", "13:40", "13:41", "13:42", "13:43", "13:44"]
    volumes = [1000.0, 1000.0, 1000.0, 900.0, 900.0, 900.0]
    rows: list[dict[str, object]] = []
    for label, close, volume in zip(labels, completed_closes, volumes, strict=True):
        rows.append(
            {
                "code": code,
                "time": label,
                "close": close,
                "volume": volume,
                "amount": round(close * volume, 2),
                "named_pivot": pivot,
            }
        )
    rows.append(
        {
            "code": code,
            "time": "13:45",
            "close": completed_closes[-1] - 1.0,
            "volume": 1500.0,
            "amount": round((completed_closes[-1] - 1.0) * 1500.0, 2),
            "named_pivot": pivot + 1.0,
        }
    )
    return rows


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_full_market_inputs(
    tmp_path: Path,
    *,
    candidates: list[dict[str, object]],
    manifest_overrides: dict[str, object] | None = None,
    market_snapshot_rows: list[dict[str, object]] | None = None,
    daily_rows: list[dict[str, object]] | None = None,
    classification_rows: list[dict[str, object]] | None = None,
    industry_rows: list[dict[str, object]] | None = None,
    minute_rows: list[dict[str, object]] | None = None,
    market_breadth_payload: dict[str, object] | None = None,
    data_audit_payload: dict[str, object] | None = None,
    handoff_generated_at: str = "2026-09-03T13:45:00+08:00",
    union_trade_date: str = "2026-09-03",
    union_observed_at: str = "2026-09-03T13:43:00+08:00",
) -> dict[str, Path]:
    data_root = tmp_path / "daily_runs"
    run_dir = data_root / "2026-09-03_134500_full_market"
    run_dir.mkdir(parents=True, exist_ok=True)

    candidate_union_path = _write_json(
        tmp_path / "candidate_union.json",
        {
            "schema_version": "1.0.0",
            "trade_date": union_trade_date,
            "observed_at": union_observed_at,
            "market_rows": 5546,
            "funnel": {"initial": 5546, "candidates": len(candidates)},
            "candidates": candidates,
        },
    )

    snapshot_rows = market_snapshot_rows or [
        {
            "code": row["code"],
            "name": row.get("name", f"股票{row['code']}"),
            "market": row.get("market", "sh"),
            "price": row["price"],
            "pre_close": row.get("pre_close", 50.0),
            "open": row.get("open", 51.0),
            "high": row.get("high", 52.6),
            "low": row.get("low", 51.0),
            "change_pct": row.get("change_pct", 4.8),
            "turnover_rate": row.get("turnover_rate", 3.0),
            "amount": row.get("amount", 80_000_000),
            "volume": row.get("volume", 1_000_000),
            "is_untradable": row.get("is_untradable", False),
        }
        for row in candidates
    ]
    _write_json(
        run_dir / "market_snapshot.json",
        {"schema_version": 1, "generated_at": handoff_generated_at, "rows": snapshot_rows},
    )

    pd.DataFrame(
        daily_rows
        or [
            {"code": row["code"], "ma5": 51.0, "ma10": 50.8, "ma20": 50.2, "turnover_rate": 3.0}
            for row in candidates
        ]
    ).to_csv(run_dir / "daily_indicators.csv", index=False, encoding="utf-8")

    pd.DataFrame(
        classification_rows
        or [
            {
                "code": row["code"],
                "industry": row.get("industry", "银行"),
                "concepts": row.get("concepts", "国企改革"),
                "supply_chain": row.get("supply_chain", "金融"),
            }
            for row in candidates
        ]
    ).to_csv(run_dir / "classification_map.csv", index=False, encoding="utf-8")

    pd.DataFrame(
        industry_rows
        or [
            {
                "industry": row.get("industry", "银行"),
                "advance_ratio": 0.62,
                "avg_change_pct": 0.012,
                "component_count": 35,
            }
            for row in candidates
        ]
    ).to_csv(run_dir / "industry_agg.csv", index=False, encoding="utf-8")

    _write_json(
        run_dir / "market_breadth.json",
        market_breadth_payload
        or {
            "full_market": {
                "advance_count": 3450,
                "decline_count": 1800,
                "unchanged_count": 296,
                "total": 5546,
            }
        },
    )
    _write_json(
        run_dir / "data_audit.json",
        data_audit_payload
        or {
            "schema_version": 1,
            "trade_date": union_trade_date,
            "quality_status": "data_ready",
            "generated_at": handoff_generated_at,
        },
    )

    minute_frame = pd.DataFrame(
        minute_rows
        or [
            minute_row
            for row in candidates
            for minute_row in _minute_rows_for(row["code"], pivot=row.get("named_pivot", 52.0))
        ]
    )
    minute_frame.to_parquet(run_dir / "intraday_minutes.parquet", index=False)

    latest_payload: dict[str, object] = {
        "schema_version": 1,
        "generated_at": handoff_generated_at,
        "market_snapshot_path": str((run_dir / "market_snapshot.json").resolve()),
        "daily_indicators_path": str((run_dir / "daily_indicators.csv").resolve()),
        "classification_map_path": str((run_dir / "classification_map.csv").resolve()),
        "industry_agg_path": str((run_dir / "industry_agg.csv").resolve()),
        "market_breadth_path": str((run_dir / "market_breadth.json").resolve()),
        "intraday_minutes_path": str((run_dir / "intraday_minutes.parquet").resolve()),
        "data_audit_path": str((run_dir / "data_audit.json").resolve()),
    }
    latest_payload.update(manifest_overrides or {})
    _write_json(data_root / "latest_codex_input.json", latest_payload)

    return {
        "data_root": data_root,
        "run_dir": run_dir,
        "candidate_union_path": candidate_union_path,
    }


def test_orchestrate_full_market_t1_marks_candidate_with_missing_daily_as_data_insufficient(tmp_path: Path):
    candidates = [
        {
            "code": "600000",
            "name": "浦发银行",
            "market": "sh",
            "price": 52.4,
            "amount": 80_000_000,
            "opportunity_score": 70.0,
            "price_band": "production",
            "candidate_reason": ["trend_full"],
            "buy_low": 51.8,
            "buy_high": 52.6,
            "chase_line": 52.8,
            "protect": 51.2,
            "rr_ratio": 1.8,
            "named_pivot": 52.0,
        },
        {
            "code": "600001",
            "name": "邯郸钢铁",
            "market": "sh",
            "price": 52.1,
            "amount": 60_000_000,
            "opportunity_score": 69.0,
            "price_band": "production",
            "candidate_reason": ["trend_full"],
            "buy_low": 51.6,
            "buy_high": 52.3,
            "chase_line": 52.5,
            "protect": 51.0,
            "rr_ratio": 1.7,
            "named_pivot": 51.9,
        },
    ]
    fixture = _write_full_market_inputs(
        tmp_path,
        candidates=candidates,
        daily_rows=[{"code": "600000", "ma5": 51.0, "ma10": 50.8, "ma20": 50.2, "turnover_rate": 3.0}],
    )

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )

    assert decision["global_status"] == "decision_ready"
    rejected = {row["code"]: row for row in decision["rejected"]}
    assert rejected["600001"]["decision"] == "data_insufficient"
    assert "daily_missing" in rejected["600001"]["reason_codes"]
    assert (tmp_path / "decision.json").is_file()


def test_orchestrate_full_market_t1_accepts_timestamp_only_intraday_fixture(tmp_path: Path):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "protection_constructible": True,
        "fee_adjusted_rr": 1.8,
        "named_pivot": 44.8,
    }
    minute_rows = [
        {
            "code": "000001",
            "timestamp": f"2026-09-03T{label}:00+08:00",
            "close": close,
            "volume": volume,
            "amount": round(close * volume, 2),
            "named_pivot": 44.8,
        }
        for label, close, volume in (
            ("13:39", 44.45, 1000.0),
            ("13:40", 44.50, 1000.0),
            ("13:41", 44.52, 1000.0),
            ("13:42", 44.80, 900.0),
            ("13:43", 44.88, 900.0),
            ("13:44", 45.00, 900.0),
            ("13:45", 43.80, 1500.0),
        )
    ]
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate], minute_rows=minute_rows)

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )

    shadow = decision["shadow"][0]
    assert shadow["decision"] == "shadow_watch"
    assert shadow["minute_evidence"]["last_completed_minute"] == "13:44"
    assert shadow["minute_evidence"]["confirmed"] is True


def test_orchestrate_full_market_t1_requires_explicit_protection_constructibility(tmp_path: Path):
    candidate = {
        "code": "600000",
        "name": "浦发银行",
        "market": "sh",
        "price": 52.4,
        "amount": 80_000_000,
        "opportunity_score": 70.0,
        "price_band": "production",
        "candidate_reason": ["trend_full"],
        "buy_low": 51.8,
        "buy_high": 52.6,
        "chase_line": 52.8,
        "protect": 51.2,
        "named_pivot": 52.0,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )

    row = decision["rejected"][0]
    assert row["decision"] == "data_insufficient"
    assert "protection_constructibility_missing" in row["reason_codes"]


def test_orchestrate_full_market_t1_requires_explicit_fee_adjusted_rr(tmp_path: Path):
    candidate = {
        "code": "600000",
        "name": "浦发银行",
        "market": "sh",
        "price": 52.4,
        "amount": 80_000_000,
        "opportunity_score": 70.0,
        "price_band": "production",
        "candidate_reason": ["trend_full"],
        "buy_low": 51.8,
        "buy_high": 52.6,
        "chase_line": 52.8,
        "protect": 51.2,
        "protection_constructible": True,
        "rr_ratio": 9.9,
        "named_pivot": 52.0,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )

    row = decision["rejected"][0]
    assert row["decision"] == "data_insufficient"
    assert "fee_adjusted_rr_missing" in row["reason_codes"]


def test_orchestrate_full_market_t1_uses_only_declared_inputs_for_metadata_and_checksums(tmp_path: Path):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "rr_ratio": 1.8,
        "named_pivot": 44.8,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])
    fallback_path = fixture["data_root"] / "classification_map.csv"
    pd.DataFrame([{"code": "999999", "industry": "fallback"}]).to_csv(fallback_path, index=False, encoding="utf-8")

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=tmp_path / "decision.json",
    )

    declared_inputs = decision["input_metadata"]["declared_inputs"]
    assert set(declared_inputs) == {
        "market_snapshot_path",
        "daily_indicators_path",
        "classification_map_path",
        "industry_agg_path",
        "market_breadth_path",
        "intraday_minutes_path",
        "data_audit_path",
    }
    assert all(record["path"] != str(fallback_path.resolve()) for record in declared_inputs.values())
    for record in declared_inputs.values():
        payload = Path(record["path"]).read_bytes()
        assert record["sha256"] == __import__("hashlib").sha256(payload).hexdigest()


@pytest.mark.parametrize("mode", ["invalid_decision", "replace_failure"])
def test_orchestrate_full_market_t1_never_writes_shadow_ledger_when_output_finalize_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "rr_ratio": 1.8,
        "named_pivot": 44.8,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])
    output_path = tmp_path / "decision.json"
    ledger_path = fixture["data_root"] / "shadow" / "full_market_t1_shadow.jsonl"

    if mode == "invalid_decision":
        monkeypatch.setattr(
            full_market_t1,
            "build_full_market_decision",
            lambda *args, **kwargs: {"schema_version": "1.0.0", "shadow": [{"code": "000001", "buyable": True}]},
        )
    else:
        monkeypatch.setattr("strategies.full_market_t1.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))

    with pytest.raises((ValueError, OSError)):
        orchestrate_full_market_t1(
            data_root=fixture["data_root"],
            candidate_union_path=fixture["candidate_union_path"],
            decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
            output_path=output_path,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".tmp").exists()
    assert not ledger_path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": None, "decision_rule_version": "1.0.0", "execution_rule_version": "1.0.0"},
        {
            "schema_version": "1.0.0",
            "decision_rule_version": "1.0.0",
            "execution_rule_version": "1.0.0",
            "input_metadata": {},
            "summary": [],
            "audit_rows": [],
            "executable": [],
            "watch": [],
            "rejected": [],
            "shadow": [],
            "only_choose_one": None,
        },
        {
            "schema_version": "1.0.0",
            "decision_rule_version": "1.0.0",
            "execution_rule_version": "1.0.0",
            "input_metadata": {"declared_inputs": {}, "candidate_union": {}},
            "summary": {"executable": -1, "executable_exposed": 0, "watch": 0, "rejected": 0, "shadow_count": 0, "evaluated": 0},
            "audit_rows": [],
            "executable": [],
            "watch": [],
            "rejected": [],
            "shadow": [],
            "only_choose_one": "bad",
        },
        {
            "schema_version": "1.0.0",
            "decision_rule_version": "1.0.0",
            "execution_rule_version": "1.0.0",
            "input_metadata": {"declared_inputs": {}, "candidate_union": {}},
            "summary": {"executable": 0, "executable_exposed": 0, "watch": 0, "rejected": 0, "shadow_count": 1, "evaluated": 1},
            "audit_rows": [{"code": "000001", "decision": "shadow_watch", "production_buyable": False, "buyable": False, "only_choose_one_eligible": False, "reason_codes": []}],
            "executable": [],
            "watch": [],
            "rejected": [],
            "shadow": [{"code": "000001", "decision": "shadow_watch", "production_buyable": False, "buyable": True, "only_choose_one_eligible": False, "reason_codes": []}],
            "only_choose_one": None,
        },
        {
            "schema_version": "1.0.0",
            "decision_rule_version": "1.0.0",
            "execution_rule_version": "1.0.0",
            "input_metadata": {
                "candidate_union": {"path": "candidate.json", "sha256": "abc"},
                "declared_inputs": {"market_snapshot_path": {"path": "snapshot.json"}},
            },
            "summary": {"executable": 0, "executable_exposed": 0, "watch": 0, "rejected": 0, "shadow_count": 0, "evaluated": 1},
            "audit_rows": [{"code": "000001", "decision": "reject", "production_buyable": False, "buyable": False, "only_choose_one_eligible": False, "reason_codes": []}],
            "executable": [],
            "watch": [],
            "rejected": [{"code": "000001", "decision": "reject", "production_buyable": False, "buyable": False, "only_choose_one_eligible": False, "reason_codes": []}],
            "shadow": [],
            "only_choose_one": None,
        },
    ],
)
def test_orchestrate_full_market_t1_rejects_malformed_decision_payloads_without_writing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "protection_constructible": True,
        "fee_adjusted_rr": 1.8,
        "named_pivot": 44.8,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])
    output_path = tmp_path / "decision.json"
    ledger_path = fixture["data_root"] / "shadow" / "full_market_t1_shadow.jsonl"
    monkeypatch.setattr(full_market_t1, "build_full_market_decision", lambda *args, **kwargs: payload)

    with pytest.raises(ValueError):
        orchestrate_full_market_t1(
            data_root=fixture["data_root"],
            candidate_union_path=fixture["candidate_union_path"],
            decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
            output_path=output_path,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".tmp").exists()
    assert not ledger_path.exists()


def _valid_decision_payload_for_validation() -> dict[str, object]:
    audit_rows = [
        {
            "code": "600001",
            "price_band": "production",
            "buyable": True,
            "only_choose_one_eligible": True,
            "production_buyable": True,
            "decision": "executable_candidate",
            "reason_codes": [],
            "execution_score": 82.0,
            "opportunity_score": 70.0,
            "fee_adjusted_rr": 1.8,
            "amount": 50_000_000,
        },
        {
            "code": "600002",
            "price_band": "production",
            "buyable": False,
            "only_choose_one_eligible": False,
            "production_buyable": False,
            "decision": "reject",
            "reason_codes": ["buy_zone_not_ready"],
            "execution_score": 70.0,
            "opportunity_score": 60.0,
            "fee_adjusted_rr": 1.4,
            "amount": 40_000_000,
        },
    ]
    return {
        "schema_version": "1.0.0",
        "decision_rule_version": "1.0.0",
        "execution_rule_version": "1.0.0",
        "trade_date": "2026-09-03",
        "decision_at": "2026-09-03T13:45:00+08:00",
        "observed_at": "2026-09-03T13:43:00+08:00",
        "global_status": "decision_ready",
        "input_metadata": {
            "candidate_union": {"path": "candidate.json", "sha256": "abc"},
            "declared_inputs": {"market_snapshot_path": {"path": "snapshot.json", "sha256": "def"}},
        },
        "summary": {
            "executable": 1,
            "executable_exposed": 1,
            "watch": 0,
            "rejected": 1,
            "shadow_count": 0,
            "evaluated": 2,
        },
        "audit_rows": audit_rows,
        "executable": [audit_rows[0]],
        "watch": [],
        "rejected": [audit_rows[1]],
        "shadow": [],
        "only_choose_one": "600001",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "entry_active",
        "production_buyable",
        "buyable",
        "only_choose_one_eligible",
        "only_choose_one",
    ],
)
def test_validator_rejects_actions_in_data_not_ready_payload(mutation: str):
    payload = _valid_decision_payload_for_validation()
    payload["global_status"] = "data_not_ready"
    payload["only_choose_one"] = None
    payload["audit_rows"][0]["production_buyable"] = False
    payload["audit_rows"][0]["buyable"] = False
    payload["audit_rows"][0]["only_choose_one_eligible"] = False
    payload["audit_rows"][0]["decision"] = "data_insufficient"
    payload["summary"].update({"executable": 0, "executable_exposed": 0, "rejected": 2})
    payload["executable"] = []
    payload["rejected"] = payload["audit_rows"]

    if mutation == "entry_active":
        payload["audit_rows"][0]["entry_state"] = "entry_active"
    elif mutation == "only_choose_one":
        payload["only_choose_one"] = "600001"
    else:
        payload["audit_rows"][0][mutation] = True

    with pytest.raises(ValueError, match="data_not_ready"):
        full_market_t1._validate_decision_payload(payload)


@pytest.mark.parametrize("only_choose_one", ["999999", "600002"])
def test_orchestrate_full_market_t1_rejects_invalid_only_choose_one_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    only_choose_one: str,
):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "protection_constructible": True,
        "fee_adjusted_rr": 1.8,
        "named_pivot": 44.8,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])
    output_path = tmp_path / "decision.json"
    ledger_path = fixture["data_root"] / "shadow" / "full_market_t1_shadow.jsonl"
    payload = _valid_decision_payload_for_validation()
    payload["only_choose_one"] = only_choose_one
    monkeypatch.setattr(full_market_t1, "build_full_market_decision", lambda *args, **kwargs: payload)

    with pytest.raises(ValueError, match="only_choose_one"):
        orchestrate_full_market_t1(
            data_root=fixture["data_root"],
            candidate_union_path=fixture["candidate_union_path"],
            decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
            output_path=output_path,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".tmp").exists()
    assert not ledger_path.exists()


def test_orchestrate_full_market_t1_accepts_valid_only_choose_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    candidate = {
        "code": "000001",
        "name": "平安银行",
        "market": "sz",
        "price": 45.0,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": "shadow_40_50",
        "candidate_reason": ["shadow_watch"],
        "buy_low": 44.5,
        "buy_high": 45.2,
        "chase_line": 45.3,
        "protect": 43.8,
        "protection_constructible": True,
        "fee_adjusted_rr": 1.8,
        "named_pivot": 44.8,
    }
    fixture = _write_full_market_inputs(tmp_path, candidates=[candidate])
    output_path = tmp_path / "decision.json"
    payload = _valid_decision_payload_for_validation()
    monkeypatch.setattr(full_market_t1, "build_full_market_decision", lambda *args, **kwargs: payload)

    decision = orchestrate_full_market_t1(
        data_root=fixture["data_root"],
        candidate_union_path=fixture["candidate_union_path"],
        decision_at=datetime(2026, 9, 3, 13, 45, tzinfo=TZ_SHANGHAI),
        output_path=output_path,
    )

    assert decision["only_choose_one"] == "600001"
    assert output_path.exists()


def test_orchestrate_full_market_t1_replays_saved_2026_09_03_inputs(tmp_path: Path):
    expected_summary = _saved_replay_expected_summary()
    decision, source_paths = _replay_saved_full_market_t1_inputs(tmp_path)

    saved_manifest = _saved_replay_manifest(source_paths)
    data_audit = _saved_replay_data_audit(source_paths)
    replay_manifest = json.loads((tmp_path / "latest_codex_input.json").read_text(encoding="utf-8"))
    assert decision["trade_date"] == expected_summary["trade_date"]
    assert decision["global_status"] == expected_summary["global_status"]
    assert decision["only_choose_one"] == expected_summary["only_choose_one"]
    assert saved_manifest["generated_at"] == expected_summary["manifest_generated_at"]
    assert replay_manifest["generated_at"] == expected_summary["manifest_generated_at"]
    assert data_audit["quality_status"] == "partial"
    assert data_audit["quality_reason_codes"] == expected_summary["quality_reason_codes"]
    assert "classification_coverage_insufficient" in data_audit["quality_reason_codes"]
    assert decision["summary"] == {
        "executable": expected_summary["executable"],
        "executable_exposed": expected_summary["executable_exposed"],
        "watch": expected_summary["watch"],
        "rejected": expected_summary["rejected"],
        "shadow_count": expected_summary["shadow_count"],
        "evaluated": expected_summary["evaluated"],
    }
    assert decision["market"]["full_market"]["total"] == expected_summary["market_rows"]
    assert decision["market"]["critical_ready"] is True
    assert decision["shadow"] and [row["code"] for row in decision["shadow"]] == expected_summary["shadow_codes"]
    assert all(row["production_buyable"] is False for row in decision["shadow"])
    assert all(row["buyable"] is False for row in decision["shadow"])
    assert all(row["only_choose_one_eligible"] is False for row in decision["shadow"])
    evaluated = [row for row in decision["audit_rows"] if row["dual_axis"]["status"] == "evaluated"]
    assert evaluated, "saved replay must exercise the real dual-axis lifecycle"
    for row in evaluated:
        assert row["strategy_channel"] in {
            "stable_pullback", "trend_recovery", "high_momentum", "oversold_theme_reversal"
        }
        assert row["lifecycle_channel"]
        assert row["channel_mapping_version"] == "frozen_v2_to_lifecycle_v1"
        assert row["entry_state_trajectory"]
    _assert_reason_codes_present(decision["watch"])
    _assert_reason_codes_present(decision["rejected"])
    _assert_reason_codes_present(
        [row for row in decision["audit_rows"] if row["decision"] not in {"executable_candidate"}]
    )

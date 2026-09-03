from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from strategies.full_market_t1 import (
    CandidateObjectiveData,
    build_candidate_union,
    build_minute_evidence,
    candidate_data_status,
    classify_price_band,
    compute_execution_score,
    write_candidate_union,
)


TZ_SHANGHAI = timezone(timedelta(hours=8))


def _base_snapshot(**overrides):
    payload = {
        "code": "1234",
        "price": 10.0,
        "change_pct": 3.2,
        "turnover_rate": 4.8,
        "low": 9.7,
        "high": 10.8,
    }
    payload.update(overrides)
    return payload


def _base_daily(**overrides):
    payload = {
        "ma5": 9.9,
        "ma10": 9.8,
    }
    payload.update(overrides)
    return payload


def _base_industry(**overrides):
    payload = {"industry": "bank", "advance_ratio": 0.62}
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
                "opportunity_tags": "trend_full|vr_good(2.0)",
            },
            {
                "code": "000001",
                "market": "sz",
                "name": "平安银行",
                "price": 45.0,
                "amount": 60_000_000,
                "opportunity_tags": ["shadow_watch"],
            },
            {
                "code": "600519",
                "market": "sh",
                "name": "贵州茅台",
                "price": 1800.0,
                "amount": 90_000_000,
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

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketbase.plan_lifecycle import freeze_plan_bundle, is_currently_buyable


CN = timezone(timedelta(hours=8))
PUBLISHED = datetime(2026, 9, 16, 11, 40, tzinfo=CN)


def _decision() -> dict:
    return {
        "trade_date": "2026-09-16",
        "observed_at": "2026-09-16T11:39:58+08:00",
        "decision_at": "2026-09-16T11:40:00+08:00",
        "research_status": "ready",
        "execution_status": "ready",
        "pipeline_status": "completed",
        "snapshot_age_seconds": 30.0,
        "research_choices": [
            {
                "rank": 1,
                "code": "600000",
                "name": "浦发银行",
                "price": 10.5,
                "opportunity_score": 75.0,
                "execution_score": 70.0,
                "buyable": False,
                "reason_codes": ["minute_confirmation_pending"],
                "research_reference": {
                    "buy_low": 9.8,
                    "buy_high": 10.2,
                    "no_chase_price": 10.8,
                    "protect": 9.5,
                },
            }
        ],
        "audit_rows": [
            {
                "code": "600000",
                "name": "浦发银行",
                "price": 10.5,
                "opportunity_score": 75.0,
                "execution_score": 70.0,
                "buyable": False,
                "buy_low": 9.8,
                "buy_high": 10.2,
                "chase_line": 10.8,
                "protect": 9.5,
                "confirm_price": 10.25,
                "sell1_low": 10.9,
                "sell1_high": 11.1,
                "sell2_low": 11.4,
                "sell2_high": 11.8,
                "reason_codes": ["minute_confirmation_pending"],
            }
        ],
    }


def test_freezes_complete_1130_plan_and_marks_price_above_zone_waiting_for_pullback():
    bundle = freeze_plan_bundle(
        _decision(), slot="1130", published_at=PUBLISHED, source_path=Path("decision.json")
    )

    assert bundle["schema_version"] == "1.0"
    assert bundle["plan_status"] == "ready"
    assert bundle["first_published_at"] == PUBLISHED.isoformat()
    plan = bundle["plans"][0]
    assert plan["plan_id"] == "2026-09-16-1130-600000-v1"
    assert plan["first_published_at"] == PUBLISHED.isoformat()
    assert plan["buy_zone"] == {"low": 9.8, "high": 10.2}
    assert plan["confirmation_price"] == 10.25
    assert plan["no_chase_price"] == 10.8
    assert plan["protection_price"] == 9.5
    assert plan["target_1"] == {"low": 10.9, "high": 11.1}
    assert plan["target_2"] == {"low": 11.4, "high": 11.8}
    assert plan["valid_until"] == "2026-09-16T14:50:00+08:00"
    assert plan["publication_state"] == "等待回踩"
    assert plan["currently_buyable"] is False


@pytest.mark.parametrize(
    ("mutate_plan", "mutate_row", "mutate_decision"),
    [
        (lambda plan: plan.update(buy_zone={"low": 9.8, "high": 10.0}), lambda row: None, lambda decision: None),
        (lambda plan: None, lambda row: row.update(execution_score=67.99), lambda decision: None),
        (lambda plan: None, lambda row: row.update(buyable=False), lambda decision: None),
        (lambda plan: None, lambda row: None, lambda decision: decision.update(snapshot_age_seconds=120.01)),
        (lambda plan: None, lambda row: None, lambda decision: decision.update(execution_status="stale_snapshot")),
    ],
)
def test_current_buy_requires_fresh_in_zone_score_68_buyable_and_all_gates(
    mutate_plan, mutate_row, mutate_decision
):
    plan = {
        "buy_zone": {"low": 9.8, "high": 10.2},
        "no_chase_price": 10.8,
        "valid_until": "2026-09-16T14:50:00+08:00",
    }
    row = {"price": 10.1, "execution_score": 68.0, "buyable": True, "reason_codes": []}
    decision = {"snapshot_age_seconds": 120.0, "execution_status": "ready"}
    assert is_currently_buyable(plan, row, decision, decision_at=PUBLISHED) is True

    bad_plan, bad_row, bad_decision = deepcopy(plan), deepcopy(row), deepcopy(decision)
    mutate_plan(bad_plan)
    mutate_row(bad_row)
    mutate_decision(bad_decision)
    assert is_currently_buyable(bad_plan, bad_row, bad_decision, decision_at=PUBLISHED) is False


def test_missing_confirmation_price_keeps_plan_but_marks_bundle_incomplete():
    decision = _decision()
    decision["audit_rows"][0]["confirm_price"] = None

    bundle = freeze_plan_bundle(
        decision, slot="1130", published_at=PUBLISHED, source_path=Path("decision.json")
    )

    assert bundle["plan_status"] == "incomplete"
    assert bundle["plans"][0]["confirmation_price"] is None
    assert "confirmation_price" in bundle["plans"][0]["missing_fields"]

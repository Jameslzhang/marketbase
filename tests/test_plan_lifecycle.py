from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pandas as pd

from marketbase.plan_lifecycle import freeze_plan_bundle, is_currently_buyable, review_frozen_plans


CN = timezone(timedelta(hours=8))
PUBLISHED = datetime(2026, 9, 16, 11, 40, tzinfo=CN)


def test_invalid_target_is_not_presented_as_waiting_for_pullback():
    decision = _decision()
    decision["audit_rows"][0].update(sell1_low=10., sell1_high=10.1)
    bundle = freeze_plan_bundle(decision, slot="1130", published_at=PUBLISHED,
                                source_path=Path("decision.json"))
    assert bundle["plan_status"] == "incomplete"
    assert bundle["plans"][0]["publication_state"] == "计划无效，目标或保护结构不成立"
    assert "target_1_not_above_entry" in bundle["plans"][0]["validation_errors"]


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
        (lambda plan: None, lambda row: row.update(reason_codes=["minute_confirmation_pending"]), lambda decision: None),
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


def _review_plan(code: str, low: float, high: float, chase: float, protect: float) -> dict:
    return {
        "plan_id": f"2026-09-16-1130-{code}-v1",
        "code": code,
        "name": code,
        "rank": 1,
        "first_published_at": PUBLISHED.isoformat(),
        "buy_zone": {"low": low, "high": high},
        "confirmation_price": high,
        "no_chase_price": chase,
        "protection_price": protect,
        "valid_until": "2026-09-16T14:50:00+08:00",
        "opportunity_score": 75.0,
        "execution_score": None,
    }


def test_reviews_every_frozen_plan_with_post_publication_minutes_and_keeps_removed_rows():
    bundle = {
        "plans": [
            _review_plan("600001", 10.0, 10.2, 10.8, 9.5),
            _review_plan("600002", 20.0, 20.2, 20.8, 19.5),
            _review_plan("600003", 30.0, 30.2, 30.8, 29.5),
            _review_plan("600004", 40.0, 40.2, 40.8, 39.5),
            _review_plan("600005", 50.0, 50.2, 50.8, 49.5),
            _review_plan("600006", 60.0, 60.2, 60.8, 59.5),
        ]
    }
    rows = []
    for code, low, high, close in [
        ("600001", 10.3, 10.4, 10.35),  # 未触及
        ("600002", 20.1, 20.3, 20.15),  # 触及，但未通过当前执行门禁
        ("600003", 30.0, 30.2, 30.1),  # 当前可执行
        ("600004", 40.1, 40.9, 40.85),  # 越过禁追线
        ("600005", 49.4, 50.0, 49.45),  # 跌破保护位
    ]:
        rows.append(
            {
                "code": code,
                "timestamp": "2026-09-16T13:30:00+08:00",
                "low": low,
                "high": high,
                "close": close,
                "volume": 100.0,
            }
        )
    decision = {
        "execution_status": "ready",
        "snapshot_age_seconds": 30.0,
        "research_choices": [
            {"rank": 1, "code": "600002"},
            {"rank": 2, "code": "600003"},
            {"rank": 3, "code": "600004"},
        ],
        "audit_rows": [
            {"code": "600001", "price": 10.35, "execution_score": 70.0, "buyable": False},
            {"code": "600002", "price": 20.15, "execution_score": 60.0, "buyable": False},
            {"code": "600003", "price": 30.1, "execution_score": 70.0, "buyable": True},
            {"code": "600004", "price": 40.85, "execution_score": 70.0, "buyable": False},
            {"code": "600005", "price": 49.45, "execution_score": 70.0, "buyable": False},
            {"code": "600006", "price": 60.1, "execution_score": 70.0, "buyable": False},
        ],
    }

    reviews = review_frozen_plans(
        bundle,
        decision,
        pd.DataFrame(rows),
        reviewed_at=datetime(2026, 9, 16, 13, 45, tzinfo=CN),
    )

    by_code = {review["code"]: review for review in reviews}
    assert by_code["600001"]["review_status"] == "未触及"
    assert by_code["600002"]["review_status"] == "触及未确认"
    assert by_code["600003"]["review_status"] == "当前可执行"
    assert by_code["600004"]["review_status"] == "已经错过"
    assert by_code["600005"]["review_status"] == "已失效"
    assert by_code["600006"]["review_status"] == "证据不足"
    assert by_code["600001"]["rank_change"] == "跌出前三"
    assert by_code["600001"]["plan_id"] == "2026-09-16-1130-600001-v1"
    assert len(reviews) == 6


def test_naive_minute_timestamps_are_interpreted_as_china_time():
    plan = _review_plan("600000", 10.0, 10.2, 10.8, 9.5)
    decision = {
        "execution_status": "ready", "snapshot_age_seconds": 30.0,
        "research_choices": [],
        "audit_rows": [{
            "code": "600000", "price": 10.1, "execution_score": 60.0,
            "buyable": False,
        }],
    }
    minutes = pd.DataFrame([{
        "code": "600000", "timestamp": "2026-09-16T13:30:00",
        "low": 10.0, "high": 10.2, "close": 10.1, "volume": 100.0,
    }])

    review = review_frozen_plans(
        {"plans": [plan]}, decision, minutes,
        reviewed_at=datetime(2026, 9, 16, 13, 45, tzinfo=CN),
    )[0]

    assert review["review_status"] == "触及未确认"
    assert review["touched_at"] == "2026-09-16T13:30:00+08:00"

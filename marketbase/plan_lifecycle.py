"""Versioned conditional-plan lifecycle for scheduled T+1 research.

The module never invents prices.  It freezes fields already present in the
same-round decision and keeps research plans separate from executable signals.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping


CN = timezone(timedelta(hours=8))
EXECUTION_SCORE_THRESHOLD = 68.0
MAX_SNAPSHOT_AGE_SECONDS = 120.0


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _row_by_code(decision: Mapping) -> dict[str, Mapping]:
    return {
        str(row.get("code", "")).zfill(6): row
        for row in decision.get("audit_rows", [])
        if isinstance(row, Mapping) and row.get("code") is not None
    }


def _valid_until(published_at: datetime, slot: str) -> datetime:
    local = published_at.astimezone(CN)
    if slot == "1530":
        return datetime.combine(local.date() + timedelta(days=1), time(14, 50), CN)
    return datetime.combine(local.date(), time(14, 50), CN)


def is_currently_buyable(
    plan: Mapping,
    row: Mapping,
    decision: Mapping,
    *,
    decision_at: datetime,
) -> bool:
    """Return true only when every frozen execution gate is currently valid."""
    zone = _mapping(plan.get("buy_zone"))
    low, high = _float(zone.get("low")), _float(zone.get("high"))
    price = _float(row.get("price"))
    score = _float(row.get("execution_score"))
    age = _float(decision.get("snapshot_age_seconds"))
    chase = _float(plan.get("no_chase_price"))
    if None in (low, high, price, score, age):
        return False
    if row.get("buyable") is not True or score < EXECUTION_SCORE_THRESHOLD:
        return False
    if decision.get("execution_status") != "ready" or age > MAX_SNAPSHOT_AGE_SECONDS:
        return False
    if not low <= price <= high or (chase is not None and price > chase):
        return False
    valid_until = plan.get("valid_until")
    if valid_until:
        try:
            expiry = datetime.fromisoformat(str(valid_until))
        except ValueError:
            return False
        if expiry.tzinfo is None or decision_at.astimezone(CN) > expiry.astimezone(CN):
            return False
    return True


def freeze_plan_bundle(
    decision: Mapping,
    *,
    slot: str,
    published_at: datetime,
    source_path: Path,
) -> dict:
    """Freeze same-round research prices into a versioned plan bundle."""
    if published_at.tzinfo is None:
        raise ValueError("published_at must be timezone-aware")
    trade_date = str(decision.get("trade_date") or published_at.astimezone(CN).date().isoformat())
    rows = _row_by_code(decision)
    plans: list[dict] = []
    for choice in decision.get("research_choices", []):
        if not isinstance(choice, Mapping):
            continue
        code = str(choice.get("code", "")).zfill(6)
        row = rows.get(code, {})
        reference = _mapping(choice.get("research_reference"))
        buy_low = _float(reference.get("buy_low", row.get("buy_low")))
        buy_high = _float(reference.get("buy_high", row.get("buy_high")))
        confirm = _float(row.get("confirm_price", row.get("confirmation_price")))
        no_chase = _float(reference.get("no_chase_price", row.get("chase_line")))
        protection = _float(reference.get("protect", row.get("protect")))
        target_1 = {"low": _float(row.get("sell1_low")), "high": _float(row.get("sell1_high"))}
        target_2 = {"low": _float(row.get("sell2_low")), "high": _float(row.get("sell2_high"))}
        price = _float(choice.get("price", row.get("price")))
        missing = []
        required = {
            "buy_low": buy_low,
            "buy_high": buy_high,
            "confirmation_price": confirm,
            "no_chase_price": no_chase,
            "protection_price": protection,
            "target_1_low": target_1["low"],
            "target_1_high": target_1["high"],
            "target_2_low": target_2["low"],
            "target_2_high": target_2["high"],
        }
        missing.extend(key for key, value in required.items() if value is None)
        expiry = _valid_until(published_at, slot)
        plan = {
            "plan_id": f"{trade_date}-{slot}-{code}-v1",
            "version": 1,
            "slot": slot,
            "code": code,
            "name": choice.get("name", row.get("name", "")),
            "rank": choice.get("rank"),
            "first_published_at": published_at.astimezone(CN).isoformat(),
            "source_path": str(source_path),
            "snapshot_price": price,
            "snapshot_time": choice.get("observed_at") or row.get("observed_at") or decision.get("observed_at"),
            "opportunity_score": choice.get("opportunity_score", row.get("opportunity_score")),
            "execution_score": choice.get("execution_score", row.get("execution_score")),
            "buy_zone": {"low": buy_low, "high": buy_high},
            "confirmation_price": confirm,
            "confirmation_condition": (
                "价格达到确认价且分钟VWAP、量能及全部执行门禁通过"
                if confirm is not None
                else "尚无可核验确认价；需分钟VWAP、量能及全部执行门禁重新确认"
            ),
            "no_chase_price": no_chase,
            "protection_price": protection,
            "target_1": target_1,
            "target_2": target_2,
            "valid_until": expiry.isoformat(),
            "invalidation_conditions": [
                "跌破保护位",
                "越过禁追线",
                "分钟VWAP或量能确认失败",
                "报价超过120秒",
                "执行分低于68或任一生产硬门禁未通过",
            ],
            "buyable_at_publication": choice.get("buyable") is True and row.get("buyable") is True,
            "missing_fields": missing,
        }
        plan["currently_buyable"] = is_currently_buyable(
            plan, {**choice, **row}, decision, decision_at=published_at
        )
        if plan["currently_buyable"]:
            state = "当前可以买"
        elif price is None or buy_low is None or buy_high is None:
            state = "计划不完整"
        elif no_chase is not None and price > no_chase:
            state = "已越过禁追线"
        elif price > buy_high:
            state = "等待回踩"
        elif price < buy_low:
            state = "低于参考区，等待重新确认"
        else:
            state = "进入参考区，等待确认"
        plan["publication_state"] = state
        plans.append(plan)
    return {
        "schema_version": "1.0",
        "trade_date": trade_date,
        "slot": slot,
        "first_published_at": published_at.astimezone(CN).isoformat(),
        "source_path": str(source_path),
        "plan_status": "ready" if plans and all(not plan["missing_fields"] for plan in plans) else "incomplete",
        "plans": plans,
    }

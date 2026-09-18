"""Versioned conditional-plan lifecycle for scheduled T+1 research.

The module never invents prices.  It freezes fields already present in the
same-round decision and keeps research plans separate from executable signals.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import math
from typing import Mapping

import pandas as pd


CN = timezone(timedelta(hours=8))
EXECUTION_SCORE_THRESHOLD = 68.0
MAX_SNAPSHOT_AGE_SECONDS = 120.0
BLOCKING_REASON_CODES = {
    "all_channels_hard_fail",
    "execution_score_below_threshold",
    "minute_confirmation_pending",
    "buy_zone_not_ready",
    "fee_adjusted_rr_below_threshold",
    "fee_adjusted_rr_missing",
    "global_data_not_ready",
    "stale_snapshot",
    "minute_missing",
    "vwap_missing",
    "execution_score_data_missing",
}


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_by_code(decision: Mapping) -> dict[str, Mapping]:
    return {
        str(row.get("code", "")).zfill(6): row
        for row in decision.get("audit_rows", [])
        if isinstance(row, Mapping) and row.get("code") is not None
    }


def _minute_timestamps_utc(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert("UTC")
    if pd.api.types.is_datetime64_dtype(parsed.dtype):
        return parsed.dt.tz_localize(CN).dt.tz_convert("UTC")

    def normalize(value: object) -> pd.Timestamp:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return pd.NaT
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(CN)
        return stamp.tz_convert("UTC")

    return values.map(normalize)


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
    if plan.get("validation_errors"):
        return False
    low, high = _float(zone.get("low")), _float(zone.get("high"))
    price = _float(row.get("price"))
    score = _float(row.get("execution_score"))
    age = _float(decision.get("snapshot_age_seconds"))
    chase = _float(plan.get("no_chase_price"))
    if None in (low, high, price, score, age):
        return False
    if row.get("buyable") is not True or score < EXECUTION_SCORE_THRESHOLD:
        return False
    if BLOCKING_REASON_CODES.intersection(str(code) for code in row.get("reason_codes", [])):
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
        validation_errors = []
        if buy_low is not None and buy_high is not None:
            if not 0 < buy_low <= buy_high:
                validation_errors.append("buy_zone_invalid")
            if protection is not None and not 0 < protection < buy_low:
                validation_errors.append("protection_not_below_entry")
            if target_1["low"] is not None and target_1["low"] <= buy_high:
                validation_errors.append("target_1_not_above_entry")
        for name, target in (("target_1", target_1), ("target_2", target_2)):
            if None not in target.values() and target["low"] > target["high"]:
                validation_errors.append(name + "_reversed")
        if target_1["high"] is not None and target_2["low"] is not None:
            if target_2["low"] <= target_1["high"]:
                validation_errors.append("target_2_not_above_target_1")
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
            "validation_errors": validation_errors,
            "target_rule_version": row.get("target_rule_version", "legacy"),
            "target_1_source": row.get("target_1_source"),
            "target_2_source": row.get("target_2_source"),
        }
        plan["currently_buyable"] = is_currently_buyable(
            plan, {**choice, **row}, decision, decision_at=published_at
        )
        if validation_errors:
            state = "计划无效，目标或保护结构不成立"
        elif plan["currently_buyable"]:
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
        "plan_status": "ready" if plans and all(not plan["missing_fields"] and not plan["validation_errors"] for plan in plans) else "incomplete",
        "plans": plans,
    }


def _rank_change(plan: Mapping, current_rank: object) -> str:
    if current_rank is None:
        return "跌出前三"
    old_rank = _float(plan.get("rank"))
    new_rank = _float(current_rank)
    if old_rank is None or new_rank is None or old_rank == new_rank:
        return "保留"
    return "晋级" if new_rank < old_rank else "降级"


def review_frozen_plans(
    bundle: Mapping,
    decision: Mapping,
    minutes: pd.DataFrame,
    *,
    reviewed_at: datetime,
) -> list[dict]:
    """Review every frozen plan using only post-publication minute evidence."""
    if reviewed_at.tzinfo is None:
        raise ValueError("reviewed_at must be timezone-aware")
    current_rows = _row_by_code(decision)
    current_ranks = {
        str(row.get("code", "")).zfill(6): row.get("rank")
        for row in decision.get("research_choices", [])
        if isinstance(row, Mapping)
    }
    minute_frame = minutes.copy() if isinstance(minutes, pd.DataFrame) else pd.DataFrame()
    if not minute_frame.empty and "code" in minute_frame:
        minute_frame["_code"] = minute_frame["code"].astype(str).str.zfill(6)
        minute_frame["_timestamp"] = _minute_timestamps_utc(minute_frame["timestamp"])
    reviews: list[dict] = []
    reviewed_utc = pd.Timestamp(reviewed_at).tz_convert("UTC")
    for plan in bundle.get("plans", []):
        if not isinstance(plan, Mapping):
            continue
        code = str(plan.get("code", "")).zfill(6)
        row = current_rows.get(code, {})
        zone = _mapping(plan.get("buy_zone"))
        buy_low, buy_high = _float(zone.get("low")), _float(zone.get("high"))
        no_chase = _float(plan.get("no_chase_price"))
        protection = _float(plan.get("protection_price"))
        confirm = _float(plan.get("confirmation_price"))
        published_text = plan.get("first_published_at")
        evidence = pd.DataFrame()
        if not minute_frame.empty and published_text:
            try:
                published = pd.Timestamp(datetime.fromisoformat(str(published_text))).tz_convert("UTC")
            except (ValueError, TypeError):
                published = None
            if published is not None:
                evidence = minute_frame.loc[
                    minute_frame["_code"].eq(code)
                    & minute_frame["_timestamp"].gt(published)
                    & minute_frame["_timestamp"].le(reviewed_utc)
                ].sort_values("_timestamp")

        touched = pd.Series(dtype=bool)
        no_chase_hits = pd.Series(dtype=bool)
        protection_hits = pd.Series(dtype=bool)
        confirm_hits = pd.Series(dtype=bool)
        if not evidence.empty and buy_low is not None and buy_high is not None:
            lows = pd.to_numeric(evidence["low"], errors="coerce")
            highs = pd.to_numeric(evidence["high"], errors="coerce")
            closes = pd.to_numeric(evidence["close"], errors="coerce")
            touched = lows.le(buy_high) & highs.ge(buy_low)
            no_chase_hits = highs.gt(no_chase) if no_chase is not None else pd.Series(False, index=evidence.index)
            protection_hits = lows.le(protection) if protection is not None else pd.Series(False, index=evidence.index)
            confirm_hits = closes.ge(confirm) if confirm is not None else pd.Series(False, index=evidence.index)

        expired = False
        valid_until = plan.get("valid_until")
        if valid_until:
            try:
                expiry = datetime.fromisoformat(str(valid_until))
                expired = expiry.tzinfo is None or reviewed_at.astimezone(CN) > expiry.astimezone(CN)
            except ValueError:
                expired = True
        current_buyable = is_currently_buyable(plan, row, decision, decision_at=reviewed_at)
        if evidence.empty:
            status, reason = "证据不足", "首次发布时间后没有可核验分钟数据"
        elif expired or bool(protection_hits.any()):
            status = "已失效"
            reason = "计划已过有效期" if expired else "发布后分钟最低价触及或跌破保护位"
        elif bool(no_chase_hits.any()):
            status, reason = "已经错过", "发布后分钟最高价越过禁追线，当前不追"
        elif current_buyable:
            status, reason = "当前可执行", "报价、买区、执行分、时效和全部生产门禁均通过"
        elif bool(touched.any()):
            status = "触及未确认"
            reason = "发布后进入参考买区，但当前完整执行门禁未通过"
        else:
            status, reason = "未触及", "发布后分钟区间未进入首次冻结买区"

        def first_time(mask: pd.Series) -> str | None:
            if mask.empty or not bool(mask.any()):
                return None
            value = evidence.loc[mask, "_timestamp"].iloc[0]
            return value.tz_convert(CN).isoformat()

        reviews.append(
            {
                "plan_id": plan.get("plan_id"),
                "code": code,
                "name": plan.get("name", row.get("name", "")),
                "first_published_at": plan.get("first_published_at"),
                "original_rank": plan.get("rank"),
                "current_rank": current_ranks.get(code),
                "rank_change": _rank_change(plan, current_ranks.get(code)),
                "original_opportunity_score": plan.get("opportunity_score"),
                "current_opportunity_score": row.get("opportunity_score"),
                "original_execution_score": plan.get("execution_score"),
                "current_execution_score": row.get("execution_score"),
                "current_price": row.get("price"),
                "reviewed_at": reviewed_at.astimezone(CN).isoformat(),
                "review_status": status,
                "review_reason": reason,
                "touched_at": first_time(touched),
                "confirmation_price_touched_at": first_time(confirm_hits),
                "no_chase_breached_at": first_time(no_chase_hits),
                "protection_breached_at": first_time(protection_hits),
                "currently_buyable": current_buyable,
                "frozen_plan": dict(plan),
            }
        )
    return reviews

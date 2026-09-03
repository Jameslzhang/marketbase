from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

PRODUCTION_MIN_PRICE = 50.0
SHADOW_MIN_PRICE = 40.0
RESTRICTED_PREFIXES = ("300", "301", "688")
SCHEMA_VERSION = "1.0.0"
EXECUTION_RULE_VERSION = "1.0.0"
REQUIRED_EXECUTION_FIELDS = (
    "dist_vwap_pct",
    "change_pct",
    "turnover_rate",
    "from_low_pct",
    "dist_high_pct",
    "amplitude_pct",
)
READINESS_MINUTE_REASONS = ("minute_missing", "vwap_missing")


@dataclass(frozen=True)
class CandidateObjectiveData:
    snapshot: Mapping
    daily: Mapping
    industry: Mapping | None
    minute: Mapping | None
    minute_evidence: Mapping
    executability: Mapping


def classify_price_band(code: str, market: str, price: float) -> str:
    normalized = str(code).zfill(6)
    if market == "bj" or normalized.startswith(RESTRICTED_PREFIXES):
        return "excluded"
    if price >= PRODUCTION_MIN_PRICE:
        return "production"
    if price >= SHADOW_MIN_PRICE:
        return "shadow_40_50"
    return "excluded"


def _to_native(value):
    if isinstance(value, list):
        return [_to_native(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_native(item) for key, item in value.items()}
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            return value
    return value


def _normalize_candidate_reason(value) -> list[object]:
    native = _to_native(value)
    if native is None:
        return []
    if isinstance(native, list):
        return [item for item in native if item not in (None, "")]
    if isinstance(native, str):
        text = native.strip()
        if not text:
            return []
        if "|" in text:
            return [part for part in (item.strip() for item in text.split("|")) if part]
        return [text]
    return [native]


def _normalize_code(code: str) -> str:
    return str(code).strip().zfill(6)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _append_unique(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _normalize_time_label(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "T" in text:
            return datetime.fromisoformat(text).strftime("%H:%M")
        if len(text) == 4 and text.isdigit():
            return f"{text[:2]}:{text[2:]}"
        if len(text) >= 5 and text[2] == ":":
            return text[:5]
        return text
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return None


def build_minute_evidence(minutes: pd.DataFrame, code: str, observed_at: datetime) -> dict[str, object]:
    normalized_code = _normalize_code(code)
    reason_codes: list[str] = []

    if minutes is None or minutes.empty:
        return {
            "code": normalized_code,
            "hold_minutes": [],
            "activity_non_contracting": False,
            "confirmed": False,
            "last_completed_minute": None,
            "vwap": None,
            "afternoon_vwap": None,
            "pivot": None,
            "named_pivot": None,
            "execution_rule_version": EXECUTION_RULE_VERSION,
            "reason_codes": ["minute_missing", "vwap_missing", "insufficient_completed_minutes"],
        }

    frame = minutes.copy()
    if "code" in frame.columns:
        frame["code"] = frame["code"].astype(str).str.strip().str.zfill(6)
        frame = frame[frame["code"] == normalized_code]

    time_column = "time"
    if "time" not in frame.columns:
        if "minute" in frame.columns:
            time_column = "minute"
        elif "timestamp" in frame.columns:
            time_column = "timestamp"
        else:
            raise ValueError("minute rows must include time, minute, or timestamp")

    frame = frame.assign(_time_label=frame[time_column].map(_normalize_time_label))
    frame = frame[frame["_time_label"].notna()].copy()
    frame["_close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    frame["_volume"] = pd.to_numeric(frame.get("volume"), errors="coerce")
    frame["_amount"] = pd.to_numeric(frame.get("amount"), errors="coerce")
    frame = frame.dropna(subset=["_close", "_volume", "_amount"])
    frame = frame.sort_values("_time_label")

    current_minute = observed_at.strftime("%H:%M")
    completed = frame[frame["_time_label"] < current_minute].copy()
    if completed.empty:
        return {
            "code": normalized_code,
            "hold_minutes": [],
            "activity_non_contracting": False,
            "confirmed": False,
            "last_completed_minute": None,
            "vwap": None,
            "afternoon_vwap": None,
            "pivot": None,
            "named_pivot": None,
            "execution_rule_version": EXECUTION_RULE_VERSION,
            "reason_codes": ["minute_missing", "vwap_missing", "insufficient_completed_minutes"],
        }

    total_volume = float(completed["_volume"].sum())
    vwap = None if total_volume <= 0 else round(float(completed["_amount"].sum() / total_volume), 4)
    afternoon = completed[completed["_time_label"] >= "13:00"]
    afternoon_volume = float(afternoon["_volume"].sum())
    afternoon_vwap = None if afternoon_volume <= 0 else round(float(afternoon["_amount"].sum() / afternoon_volume), 4)
    if vwap is None:
        reason_codes.append("vwap_missing")

    last_six = completed.tail(6).copy()
    hold = last_six.tail(3).copy()
    baseline = last_six.head(3).copy()

    named_pivot = None
    for field in ("named_pivot", "pivot"):
        if field in hold.columns:
            pivot_series = pd.to_numeric(hold[field], errors="coerce").dropna()
            if not pivot_series.empty:
                named_pivot = float(pivot_series.iloc[-1])
                break
    if named_pivot is None:
        reason_codes.append("named_pivot_missing")

    confirmed = False
    if len(completed) < 6:
        reason_codes.append("insufficient_completed_minutes")
    elif vwap is not None and named_pivot is not None:
        hold_closes = hold["_close"]
        previous3_volume = float(baseline["_volume"].sum())
        last3_volume = float(hold["_volume"].sum())
        activity_non_contracting = last3_volume >= previous3_volume * 0.85
        holds_above_references = bool(((hold_closes > vwap) & (hold_closes > named_pivot)).all())
        confirmed = activity_non_contracting and holds_above_references
    else:
        activity_non_contracting = False

    if len(completed) >= 6:
        previous3_volume = float(baseline["_volume"].sum())
        last3_volume = float(hold["_volume"].sum())
        activity_non_contracting = last3_volume >= previous3_volume * 0.85
    else:
        previous3_volume = float(baseline["_volume"].sum())
        last3_volume = float(hold["_volume"].sum())
        activity_non_contracting = False

    if len(completed) >= 6 and not activity_non_contracting:
        reason_codes.append("activity_contracting")
    if len(completed) >= 6 and vwap is not None and named_pivot is not None and not confirmed:
        if not bool((hold["_close"] > vwap).all()):
            reason_codes.append("hold_below_vwap")
        if not bool((hold["_close"] > named_pivot).all()):
            reason_codes.append("hold_below_pivot")

    return {
        "code": normalized_code,
        "hold_minutes": hold["_time_label"].tolist(),
        "activity_non_contracting": activity_non_contracting,
        "confirmed": confirmed,
        "last_completed_minute": completed["_time_label"].iloc[-1],
        "last3_volume": round(last3_volume, 4),
        "previous3_volume": round(previous3_volume, 4),
        "vwap": vwap,
        "afternoon_vwap": afternoon_vwap,
        "pivot": named_pivot,
        "named_pivot": named_pivot,
        "execution_rule_version": EXECUTION_RULE_VERSION,
        "reason_codes": reason_codes,
    }


def candidate_data_status(
    snapshot: Mapping,
    daily: Mapping,
    industry: Mapping | None,
    minute: Mapping | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not snapshot:
        _append_unique(reasons, "snapshot_missing")
    if not daily:
        _append_unique(reasons, "daily_missing")
    if not industry:
        _append_unique(reasons, "industry_missing")
    if not minute:
        _append_unique(reasons, "minute_missing")
    else:
        minute_reason_codes = minute.get("reason_codes", [])
        for reason in READINESS_MINUTE_REASONS:
            if reason in minute_reason_codes:
                _append_unique(reasons, reason)
        vwap = minute.get("vwap")
        if vwap is None and minute.get("full_day_vwap") is None:
            _append_unique(reasons, "vwap_missing")
    if reasons:
        return "data_insufficient", reasons
    return "ready", []


def compute_execution_score(evidence: Mapping[str, float]) -> float:
    missing = [field for field in REQUIRED_EXECUTION_FIELDS if field not in evidence or evidence[field] is None]
    if missing:
        raise ValueError(f"missing execution evidence: {missing[0]}")

    score = 50.0
    dist_vwap_pct = float(evidence["dist_vwap_pct"])
    change_pct = float(evidence["change_pct"])
    turnover_rate = float(evidence["turnover_rate"])
    from_low_pct = float(evidence["from_low_pct"])
    dist_high_pct = float(evidence["dist_high_pct"])
    amplitude_pct = float(evidence["amplitude_pct"])

    score += _clamp(dist_vwap_pct * 8.0, -18.0, 18.0)
    score += _clamp(change_pct * 1.8, -16.0, 16.0)
    score += _clamp(turnover_rate * 1.2, 0.0, 10.0)
    score += _clamp(from_low_pct * 1.5, 0.0, 10.0)
    score += _clamp(dist_high_pct * 3.0, -14.0, 0.0)
    if amplitude_pct > 8.0 and dist_high_pct < -3.0:
        score -= 8.0
    return round(_clamp(score, 0.0, 100.0), 2)


def build_candidate_union(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    observed_at: str,
    market_rows: int,
    funnel: dict[str, int],
) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    normalized = frame.copy()
    if "code" in normalized.columns:
        normalized["code"] = normalized["code"].astype(str).str.strip().str.zfill(6)
    if "market" not in normalized.columns:
        normalized["market"] = ""

    for row in normalized.to_dict("records"):
        candidate = {str(key): _to_native(value) for key, value in row.items()}
        code = str(candidate.get("code", "")).zfill(6)
        market = str(candidate.get("market", "") or "").strip()
        price_value = pd.to_numeric(candidate.get("price"), errors="coerce")
        price = float(price_value) if pd.notna(price_value) else float("nan")
        candidate["code"] = code
        candidate["market"] = market
        candidate["candidate_reason"] = _normalize_candidate_reason(candidate.get("opportunity_tags"))
        candidate["price_band"] = classify_price_band(code, market, price)
        candidate["production_buyable"] = False
        candidate["buyable"] = False
        candidate["only_choose_one_eligible"] = False
        candidates.append(candidate)

    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "observed_at": observed_at,
        "market_rows": int(market_rows),
        "funnel": {str(key): int(value) for key, value in funnel.items()},
        "candidates": candidates,
    }


def write_candidate_union(payload: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return path


def _native_mapping(value) -> dict[str, object]:
    native = _to_native(value)
    if isinstance(native, Mapping):
        return {str(key): item for key, item in native.items()}
    return {}


def _coerce_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_objective(value) -> CandidateObjectiveData:
    if isinstance(value, CandidateObjectiveData):
        return value
    mapping = _native_mapping(value)
    return CandidateObjectiveData(
        snapshot=_native_mapping(mapping.get("snapshot")),
        daily=_native_mapping(mapping.get("daily")),
        industry=_native_mapping(mapping.get("industry")) or None,
        minute=_native_mapping(mapping.get("minute")) or None,
        minute_evidence=_native_mapping(mapping.get("minute_evidence")),
        executability=_native_mapping(mapping.get("executability")),
    )


def _build_execution_evidence(objective: CandidateObjectiveData) -> tuple[dict[str, float] | None, list[str]]:
    evidence: dict[str, float] = {}
    missing: list[str] = []
    sources = (
        _native_mapping(objective.executability),
        _native_mapping(objective.snapshot),
        _native_mapping(objective.daily),
        _native_mapping(objective.minute),
        _native_mapping(objective.minute_evidence),
    )

    for field in REQUIRED_EXECUTION_FIELDS:
        value = None
        for source in sources:
            if field in source and source[field] is not None:
                value = _coerce_float(source[field])
                break
        if value is None:
            missing.append(field)
        else:
            evidence[field] = value

    if missing:
        return None, ["execution_score_data_missing"]
    return evidence, []


def _industry_sync(industry: Mapping | None) -> bool | None:
    industry_mapping = _native_mapping(industry)
    if "industry_sync" in industry_mapping:
        value = industry_mapping.get("industry_sync")
        if isinstance(value, bool):
            return value
        return None
    return None


def _watch_reason(reason: str) -> bool:
    return reason in {
        "opportunity_score_below_threshold",
        "execution_score_below_threshold",
        "minute_confirmation_pending",
        "buy_zone_not_ready",
        "above_no_chase_price",
        "industry_sync_pending",
        "market_breadth_below_threshold",
        "protection_not_constructible",
        "fee_adjusted_rr_below_threshold",
    }


def _sort_key(row: Mapping) -> tuple[float, float, float, float]:
    return (
        float(row.get("execution_score") or 0.0),
        float(row.get("opportunity_score") or 0.0),
        float(row.get("fee_adjusted_rr") or 0.0),
        float(row.get("amount") or 0.0),
    )


def _validate_executability_fields(executability: Mapping) -> tuple[dict[str, object], list[str]]:
    normalized = dict(executability)
    reasons: list[str] = []

    buy_low = _coerce_float(normalized.get("buy_low"))
    buy_high = _coerce_float(normalized.get("buy_high"))
    if buy_low is None or buy_high is None:
        _append_unique(reasons, "buy_zone_missing")

    no_chase_price = _coerce_float(normalized.get("no_chase_price"))
    if no_chase_price is None:
        no_chase_price = _coerce_float(normalized.get("chase_line"))
    if no_chase_price is None:
        _append_unique(reasons, "no_chase_missing")

    protection_constructible = normalized.get("protection_constructible")
    if not isinstance(protection_constructible, bool):
        _append_unique(reasons, "protection_constructibility_missing")

    is_untradable = normalized.get("is_untradable")
    if not isinstance(is_untradable, bool):
        _append_unique(reasons, "executability_status_missing")

    fee_adjusted_rr = _coerce_float(normalized.get("fee_adjusted_rr"))
    if fee_adjusted_rr is None:
        _append_unique(reasons, "fee_adjusted_rr_missing")

    normalized["buy_low"] = buy_low
    normalized["buy_high"] = buy_high
    normalized["no_chase_price"] = no_chase_price
    normalized["protection_constructible"] = protection_constructible if isinstance(protection_constructible, bool) else None
    normalized["is_untradable"] = is_untradable if isinstance(is_untradable, bool) else None
    normalized["fee_adjusted_rr"] = fee_adjusted_rr
    return normalized, reasons


def evaluate_candidate(candidate: Mapping, objective: CandidateObjectiveData, market: Mapping) -> dict[str, object]:
    candidate_row = _native_mapping(candidate)
    objective_row = _coerce_objective(objective)
    market_row = _native_mapping(market)
    minute_evidence = _native_mapping(objective_row.minute_evidence)
    industry = _native_mapping(objective_row.industry)
    executability = _native_mapping(objective_row.executability)

    code = _normalize_code(candidate_row.get("code", ""))
    price_band = str(candidate_row.get("price_band", "") or "")
    price = _coerce_float(candidate_row.get("price")) or 0.0
    opportunity_score = _coerce_float(candidate_row.get("opportunity_score")) or 0.0
    candidate_status, reason_codes = candidate_data_status(
        objective_row.snapshot,
        objective_row.daily,
        objective_row.industry,
        objective_row.minute,
    )
    if (
        objective_row.minute is None
        and minute_evidence.get("vwap") is None
        and "vwap_missing" not in reason_codes
    ):
        reason_codes.append("vwap_missing")

    execution_evidence, execution_missing_reasons = _build_execution_evidence(objective_row)
    execution_score = None
    if execution_evidence is None:
        candidate_status = "data_insufficient"
        for reason in execution_missing_reasons:
            _append_unique(reason_codes, reason)
    else:
        execution_score = compute_execution_score(execution_evidence)

    industry_sync = _industry_sync(industry)
    if objective_row.industry is not None and industry_sync is None:
        candidate_status = "data_insufficient"
        _append_unique(reason_codes, "industry_sync_missing")

    executability, executability_missing_reasons = _validate_executability_fields(executability)
    if executability_missing_reasons:
        candidate_status = "data_insufficient"
        for reason in executability_missing_reasons:
            _append_unique(reason_codes, reason)

    buy_low = executability.get("buy_low")
    buy_high = executability.get("buy_high")
    no_chase_price = executability.get("no_chase_price")
    protection_constructible = executability.get("protection_constructible")
    is_untradable = executability.get("is_untradable")
    fee_adjusted_rr = executability.get("fee_adjusted_rr")
    market_advance_ratio = _coerce_float(market_row.get("advance_ratio")) or 0.0

    row = {
        **candidate_row,
        "code": code,
        "price_band": price_band,
        "price": price,
        "opportunity_score": round(opportunity_score, 2),
        "execution_score": execution_score,
        "candidate_data_status": candidate_status,
        "market_advance_ratio": market_advance_ratio,
        "minute_evidence": minute_evidence,
        "industry_evidence": industry,
        "executability": {
            **executability,
            "buy_low": buy_low,
            "buy_high": buy_high,
            "no_chase_price": no_chase_price,
            "fee_adjusted_rr": fee_adjusted_rr,
            "protection_constructible": protection_constructible,
            "is_untradable": is_untradable,
        },
        "fee_adjusted_rr": fee_adjusted_rr,
        "production_buyable": False,
        "buyable": False,
        "only_choose_one_eligible": False,
        "reason_codes": [],
        "decision": "reject",
    }

    if candidate_status != "ready":
        row["decision"] = "data_insufficient"
        row["reason_codes"] = reason_codes
        return row

    if price_band == "excluded":
        row["reason_codes"] = ["price_band_excluded"]
        return row

    gate_failures: list[str] = []
    if opportunity_score < 65.0:
        gate_failures.append("opportunity_score_below_threshold")
    if execution_score is None or execution_score < 68.0:
        gate_failures.append("execution_score_below_threshold")
    if minute_evidence.get("confirmed") is not True:
        gate_failures.append("minute_confirmation_pending")
    if buy_low is None or buy_high is None or not (buy_low <= price <= buy_high):
        gate_failures.append("buy_zone_not_ready")
    if no_chase_price is None or price > no_chase_price:
        gate_failures.append("above_no_chase_price")
    if industry_sync is False:
        gate_failures.append("industry_sync_pending")
    if market_advance_ratio < 0.35:
        gate_failures.append("market_breadth_below_threshold")
    if protection_constructible is False:
        gate_failures.append("protection_not_constructible")
    if is_untradable is True:
        gate_failures.append("candidate_untradable")
    if fee_adjusted_rr is None or fee_adjusted_rr < 1.5:
        gate_failures.append("fee_adjusted_rr_below_threshold")

    if price_band == "shadow_40_50":
        row["reason_codes"] = ["shadow_price_band", *gate_failures]
        row["decision"] = "shadow_watch" if opportunity_score >= 55.0 else "shadow_reject"
        return row

    row["reason_codes"] = gate_failures
    if not gate_failures:
        row["production_buyable"] = True
        row["buyable"] = True
        row["only_choose_one_eligible"] = True
        row["decision"] = "executable_candidate"
        return row

    if opportunity_score >= 55.0 and all(_watch_reason(reason) for reason in gate_failures):
        row["decision"] = "conditional_watch"
        return row

    row["decision"] = "reject"
    return row


def choose_one(rows: Sequence[Mapping]) -> str | None:
    eligible = [
        row
        for row in rows
        if row.get("price_band") == "production"
        and row.get("buyable") is True
        and row.get("only_choose_one_eligible") is True
    ]
    if not eligible:
        return None
    ranked = sorted(eligible, key=_sort_key, reverse=True)
    return _normalize_code(ranked[0].get("code", ""))


def build_full_market_decision(candidate_union: Mapping, handoff: Mapping, *, decision_at: datetime) -> dict[str, object]:
    candidate_union_row = _native_mapping(candidate_union)
    handoff_row = _native_mapping(handoff)
    market = _native_mapping(handoff_row.get("market")) or _native_mapping(candidate_union_row.get("market"))
    objective_by_code_source = (
        _native_mapping(handoff_row.get("objective_by_code"))
        or _native_mapping(candidate_union_row.get("objective_by_code"))
    )

    evaluated: list[dict[str, object]] = []
    for raw_candidate in _to_native(candidate_union_row.get("candidates", [])) or []:
        candidate_row = _native_mapping(raw_candidate)
        code = _normalize_code(candidate_row.get("code", ""))
        objective = objective_by_code_source.get(code)
        if objective is None:
            objective = CandidateObjectiveData(
                snapshot={},
                daily={},
                industry=None,
                minute=None,
                minute_evidence={},
                executability={},
            )
        row = evaluate_candidate(candidate_row, objective, market)
        evaluated.append(row)

    executable_all = sorted(
        [row for row in evaluated if row.get("decision") == "executable_candidate"],
        key=_sort_key,
        reverse=True,
    )
    watch = [row for row in evaluated if row.get("decision") == "conditional_watch"]
    shadow = [row for row in evaluated if str(row.get("decision", "")).startswith("shadow_")]
    rejected = [row for row in evaluated if row.get("decision") in {"reject", "data_insufficient"}]
    executable = executable_all[:3]

    only_choose_one_code = choose_one(evaluated)

    global_status = "data_not_ready" if handoff_row.get("critical_ready") is False or market.get("critical_ready") is False else "decision_ready"
    return {
        "schema_version": candidate_union_row.get("schema_version", SCHEMA_VERSION),
        "decision_at": decision_at.isoformat(),
        "trade_date": candidate_union_row.get("trade_date") or handoff_row.get("trade_date"),
        "observed_at": candidate_union_row.get("observed_at") or handoff_row.get("observed_at"),
        "market": market,
        "global_status": global_status,
        "only_choose_one": only_choose_one_code,
        "summary": {
            "executable": len(executable_all),
            "executable_exposed": len(executable),
            "watch": len(watch),
            "rejected": len(rejected),
            "shadow_count": len(shadow),
            "evaluated": len(evaluated),
        },
        "executable": executable,
        "watch": watch,
        "rejected": rejected,
        "shadow": shadow,
        "audit_rows": evaluated,
    }

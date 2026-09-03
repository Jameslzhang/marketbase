from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Mapping

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

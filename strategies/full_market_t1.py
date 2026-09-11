from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from strategies.strategy_lifecycle.channel_identifier import ChannelIdentifier
from strategies.strategy_lifecycle.dual_axis import DualAxis, DualAxisDecision
from strategies.strategy_lifecycle.entry_state_machine import EntryState, EntryStateMachine

PRODUCTION_MIN_PRICE = 50.0
SHADOW_MIN_PRICE = 40.0
RESTRICTED_PREFIXES = ("300", "301", "688")
SCHEMA_VERSION = "1.0.0"
EXECUTION_RULE_VERSION = "1.0.0"
DECISION_RULE_VERSION = "1.2.0"
CHANNEL_MAPPING_VERSION = "frozen_v2_to_lifecycle_v1"
ATR14_PCT_INPUT_UNIT = "percent_points"
ATR14_PCT_LIFECYCLE_UNIT = "decimal_ratio"
LIFECYCLE_TO_FROZEN_CHANNEL = {
    "strong_pullback_reclaim": "stable_pullback",
    "trend_continuation": "trend_recovery",
    "high_momentum": "high_momentum",
    "sector_reversal_challenger": "oversold_theme_reversal",
}
CANDIDATE_PROTECTION_RULE_VERSION = "candidate_guardrail_formula_v1"
CANDIDATE_FEE_SOURCE = "candidate_cn_equity_fee_formula_v1"
FEE_ADJUSTED_RR_FORMULA_VERSION = "cn_equity_fee_v1"
REQUIRED_EXECUTION_FIELDS = (
    "dist_vwap_pct",
    "change_pct",
    "turnover_rate",
    "from_low_pct",
    "dist_high_pct",
    "amplitude_pct",
)
READINESS_MINUTE_REASONS = ("minute_missing", "vwap_missing")
REQUIRED_HANDOFF_INPUT_KEYS = (
    "market_snapshot_path",
    "daily_indicators_path",
    "classification_map_path",
    "market_breadth_path",
    "intraday_minutes_path",
    "data_audit_path",
)
INDUSTRY_INPUT_KEYS = ("industry_agg_path", "market_breadth_industry_path", "industry_breadth_path")


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


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _parse_iso8601(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO-8601 string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} is not parseable as ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


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


def _normalize_minute_frame(minutes: pd.DataFrame, code: str) -> pd.DataFrame:
    frame = minutes.copy()
    normalized_code = _normalize_code(code)
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
    return frame.sort_values("_time_label")


def _coerce_non_negative_int(value) -> int | None:
    native = _to_native(value)
    if isinstance(native, bool):
        return None
    if isinstance(native, int):
        return native if native >= 0 else None
    return None


def _is_finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _derive_candidate_protection_constructible(candidate: Mapping[str, object]) -> tuple[bool | None, str]:
    explicit = _coerce_bool(candidate.get("protection_constructible"))
    if isinstance(explicit, bool):
        return explicit, "explicit_candidate_field"

    protect = _first_float(candidate.get("protect"), candidate.get("protection_price"))
    buy_low = _first_float(candidate.get("buy_low"), candidate.get("buy_zone_lower"))
    buy_high = _first_float(candidate.get("buy_high"), candidate.get("buy_zone_upper"))
    no_chase_price = _first_float(candidate.get("no_chase_price"), candidate.get("chase_line"))
    if not all(_is_finite_positive(value) for value in (protect, buy_low, buy_high, no_chase_price)):
        return None, "missing"
    derived = bool(protect < buy_low <= buy_high <= no_chase_price)
    return derived, CANDIDATE_PROTECTION_RULE_VERSION


def _derive_candidate_fee_adjusted_rr(candidate: Mapping[str, object], protection_constructible: bool | None) -> tuple[float | None, str, str]:
    explicit = _coerce_float(candidate.get("fee_adjusted_rr"))
    if explicit is not None:
        source = str(candidate.get("fee_adjusted_rr_source") or "explicit_candidate_field")
        version = str(candidate.get("fee_adjusted_rr_formula_version") or FEE_ADJUSTED_RR_FORMULA_VERSION)
        return round(explicit, 4), source, version

    buy_high = _coerce_float(candidate.get("buy_high"))
    protect = _first_float(candidate.get("protect"), candidate.get("protection_price"))
    sell1_low = _coerce_float(candidate.get("sell1_low"))
    if (
        protection_constructible is not True
        or not _is_finite_positive(buy_high)
        or not _is_finite_positive(protect)
        or not _is_finite_positive(sell1_low)
        or protect >= buy_high
        or sell1_low <= buy_high
    ):
        return None, "missing", FEE_ADJUSTED_RR_FORMULA_VERSION

    gross_reward = sell1_low - buy_high
    gross_risk = buy_high - protect
    if gross_reward <= 0 or gross_risk <= 0:
        return None, "missing", FEE_ADJUSTED_RR_FORMULA_VERSION

    estimated_buy_cost = buy_high * 0.00025
    estimated_sell_cost = sell1_low * 0.00125
    adjusted_reward = gross_reward - estimated_buy_cost - estimated_sell_cost
    adjusted_risk = gross_risk + estimated_buy_cost
    if adjusted_reward <= 0 or adjusted_risk <= 0:
        return None, "missing", FEE_ADJUSTED_RR_FORMULA_VERSION
    return round(adjusted_reward / adjusted_risk, 4), CANDIDATE_FEE_SOURCE, FEE_ADJUSTED_RR_FORMULA_VERSION


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

    frame = _normalize_minute_frame(minutes, normalized_code)

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
        holds_above_references = bool(((hold_closes > vwap) & (hold_closes >= named_pivot)).all())
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
        if not bool((hold["_close"] >= named_pivot).all()):
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
        protection_constructible, protection_source = _derive_candidate_protection_constructible(candidate)
        fee_adjusted_rr, fee_rr_source, fee_rr_version = _derive_candidate_fee_adjusted_rr(candidate, protection_constructible)
        candidate["code"] = code
        candidate["market"] = market
        candidate["candidate_reason"] = _normalize_candidate_reason(candidate.get("opportunity_tags"))
        if _coerce_float(candidate.get("atr14_pct")) is not None:
            candidate["atr14_pct_unit"] = ATR14_PCT_INPUT_UNIT
        candidate["price_band"] = classify_price_band(code, market, price)
        candidate["protection_constructible"] = protection_constructible
        candidate["protection_constructible_source"] = protection_source
        candidate["fee_adjusted_rr"] = fee_adjusted_rr
        candidate["fee_adjusted_rr_source"] = fee_rr_source
        candidate["fee_adjusted_rr_formula_version"] = fee_rr_version
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


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _first_float(*values) -> float | None:
    for value in values:
        coerced = _coerce_float(value)
        if coerced is not None:
            return coerced
    return None


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


def _file_metadata(path: Path) -> dict[str, str]:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _load_handoff_manifest(data_root: str | Path, handoff_path: str | Path | None = None) -> tuple[dict[str, object], dict[str, dict[str, str]]]:
    root = Path(data_root).expanduser().resolve()
    manifest_path = Path(handoff_path).expanduser().resolve() if handoff_path is not None else root / "latest_codex_input.json"
    manifest = _read_json(manifest_path)
    metadata: dict[str, dict[str, str]] = {"handoff_manifest": _file_metadata(manifest_path)}
    for key in REQUIRED_HANDOFF_INPUT_KEYS:
        value = manifest.get(key)
        if key == "intraday_minutes_path" and value is None:
            manifest["_minute_input_issue"] = "minute_path_null"
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"handoff manifest missing required path: {key}")
        declared_path = Path(value).expanduser().resolve()
        if not declared_path.is_file():
            if key == "intraday_minutes_path":
                manifest["_minute_input_issue"] = "minute_file_missing"
                continue
            raise ValueError(f"handoff manifest declared path missing: {key}")
        metadata[key] = _file_metadata(declared_path)

    for key in INDUSTRY_INPUT_KEYS:
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            declared_path = Path(value).expanduser().resolve()
            if not declared_path.is_file():
                raise ValueError(f"handoff manifest declared path missing: {key}")
            metadata[key] = _file_metadata(declared_path)
            break
    else:
        raise ValueError("handoff manifest missing required industry evidence path")

    return manifest, metadata


def _load_snapshot_rows(path: Path) -> list[dict[str, object]]:
    payload = _read_json(path)
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("market snapshot payload must include rows")
    return [_native_mapping(row) for row in rows]


def _resolve_handoff_observed_at(manifest: Mapping) -> datetime:
    value = manifest.get("observed_at", manifest.get("generated_at"))
    return _parse_iso8601(value, label="handoff observed_at")


def _validate_contract(candidate_union: Mapping, manifest: Mapping) -> tuple[datetime, datetime]:
    trade_date = candidate_union.get("trade_date")
    if not isinstance(trade_date, str) or not trade_date:
        raise ValueError("candidate union missing trade_date")
    candidate_observed_at = _parse_iso8601(candidate_union.get("observed_at"), label="candidate union observed_at")
    handoff_observed_at = _resolve_handoff_observed_at(manifest)
    if handoff_observed_at.date().isoformat() != trade_date:
        raise ValueError("candidate union trade_date does not match handoff observation date")
    if abs((candidate_observed_at - handoff_observed_at).total_seconds()) > 20 * 60:
        raise ValueError("candidate union observed_at differs from handoff observation time by more than 20 minutes")
    return candidate_observed_at, handoff_observed_at


def _normalize_frame_code_column(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    if "code" in normalized.columns:
        normalized["code"] = normalized["code"].astype(str).str.strip().str.zfill(6)
    return normalized


def _index_records_by_code(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for row in records:
        code = row.get("code")
        if code in (None, ""):
            continue
        indexed[_normalize_code(str(code))] = {str(key): _to_native(value) for key, value in row.items()}
    return indexed


def _index_frame_by_code(frame: pd.DataFrame) -> dict[str, dict[str, object]]:
    if frame.empty or "code" not in frame.columns:
        return {}
    normalized = _normalize_frame_code_column(frame)
    indexed: dict[str, dict[str, object]] = {}
    for row in normalized.to_dict("records"):
        indexed[_normalize_code(str(row.get("code", "")))] = {str(key): _to_native(value) for key, value in row.items()}
    return indexed


def _industry_evidence_for_code(
    code: str,
    classification_by_code: Mapping[str, Mapping[str, object]],
    industry_frame: pd.DataFrame,
) -> dict[str, object] | None:
    classification = _native_mapping(classification_by_code.get(code))
    if not classification:
        return None
    industry_name = str(classification.get("industry", "") or "").strip()
    if not industry_name:
        return None
    if industry_frame.empty or "industry" not in industry_frame.columns:
        return {
            **classification,
            "industry_sync": None,
        }

    matches = industry_frame[industry_frame["industry"].astype(str).str.strip() == industry_name]
    if matches.empty:
        return {
            **classification,
            "industry_sync": None,
        }
    row = {str(key): _to_native(value) for key, value in matches.iloc[0].to_dict().items()}
    advance_ratio = _coerce_float(row.get("advance_ratio"))
    avg_change_pct = _coerce_float(row.get("avg_change_pct"))
    component_count = _coerce_float(row.get("component_count"))
    industry_sync = None
    if component_count is not None and component_count >= 20 and advance_ratio is not None and avg_change_pct is not None:
        industry_sync = advance_ratio >= 0.50 and avg_change_pct > 0
    return {
        **classification,
        "industry_sync": industry_sync,
        "advance_ratio": advance_ratio,
        "avg_change_pct": avg_change_pct,
        "component_count": int(component_count) if component_count is not None else None,
    }


def _pick_named_pivot(candidate: Mapping[str, object]) -> tuple[float | None, str | None]:
    for field in ("named_pivot", "pivot", "technical_anchor", "buy_low", "buy_high"):
        value = _coerce_float(candidate.get(field))
        if value is not None:
            return value, f"candidate.{field}"
    return None, None


def _derive_percentage(delta: float | None, denominator: float | None) -> float | None:
    if delta is None or denominator is None or denominator == 0:
        return None
    return round((delta / denominator) * 100.0, 4)


def _build_executability(candidate: Mapping[str, object], snapshot: Mapping[str, object], minute_evidence: Mapping[str, object]) -> dict[str, object]:
    candidate_row = _native_mapping(candidate)
    snapshot_row = _native_mapping(snapshot)
    minute_row = _native_mapping(minute_evidence)
    price = _coerce_float(candidate_row.get("price"))
    vwap = _coerce_float(minute_row.get("vwap"))
    low = _coerce_float(snapshot_row.get("low"))
    high = _coerce_float(snapshot_row.get("high"))
    pre_close = _coerce_float(snapshot_row.get("pre_close"))
    protect = _coerce_float(candidate_row.get("protect"))
    protection_price = _coerce_float(candidate_row.get("protection_price"))
    protection_constructible = _coerce_bool(candidate_row.get("protection_constructible"))

    is_untradable = _coerce_bool(snapshot_row.get("is_untradable"))
    if is_untradable is None and isinstance(snapshot_row.get("tradable"), bool):
        is_untradable = not bool(snapshot_row.get("tradable"))

    return {
        "buy_low": _first_float(candidate_row.get("buy_low"), candidate_row.get("buy_zone_lower")),
        "buy_high": _first_float(candidate_row.get("buy_high"), candidate_row.get("buy_zone_upper")),
        "no_chase_price": _first_float(candidate_row.get("no_chase_price"), candidate_row.get("chase_line")),
        "protection_price": protection_price if protection_price is not None else protect,
        "protection_constructible": protection_constructible,
        "fee_adjusted_rr": _coerce_float(candidate_row.get("fee_adjusted_rr")),
        "is_untradable": is_untradable,
        "dist_vwap_pct": _first_float(candidate_row.get("dist_vwap_pct"), _derive_percentage(
            None if price is None or vwap is None else price - vwap,
            vwap,
        )),
        "change_pct": _first_float(candidate_row.get("change_pct"), snapshot_row.get("change_pct")),
        "turnover_rate": _first_float(candidate_row.get("turnover_rate"), snapshot_row.get("turnover_rate")),
        "from_low_pct": _first_float(candidate_row.get("from_low_pct"), _derive_percentage(
            None if price is None or low is None else price - low,
            low,
        )),
        "dist_high_pct": _first_float(candidate_row.get("dist_high_pct"), _derive_percentage(
            None if price is None or high is None else price - high,
            high,
        )),
        "amplitude_pct": _first_float(candidate_row.get("amplitude_pct"), _derive_percentage(
            None if high is None or low is None else high - low,
            pre_close,
        )),
    }


def _build_market_context(market_breadth: Mapping[str, object], data_audit: Mapping[str, object], handoff_observed_at: datetime) -> dict[str, object]:
    payload = _native_mapping(market_breadth)
    full_market = _native_mapping(payload.get("full_market"))
    advance_count = _coerce_float(full_market.get("advance_count"))
    total = _coerce_float(full_market.get("total"))
    advance_ratio = None if advance_count is None or total in (None, 0) else advance_count / total
    quality_status = str(data_audit.get("quality_status", "") or "")
    return {
        **payload,
        "full_market": full_market,
        "advance_ratio": advance_ratio,
        "critical_ready": quality_status not in {"data_not_ready", "data_ready_static_only", ""},
        "static_ready": quality_status in {"data_ready", "data_ready_static_only", "partial"},
        "quality_status": quality_status,
        "trade_date": data_audit.get("trade_date"),
        "observed_at": handoff_observed_at.isoformat(),
    }


def _prepare_handoff(
    *,
    candidate_union: Mapping[str, object],
    manifest: Mapping[str, object],
    input_metadata: Mapping[str, dict[str, str]],
    decision_at: datetime,
) -> dict[str, object]:
    snapshot_rows = _load_snapshot_rows(Path(input_metadata["market_snapshot_path"]["path"]))
    snapshot_by_code = _index_records_by_code(snapshot_rows)
    daily_by_code = _index_frame_by_code(pd.read_csv(Path(input_metadata["daily_indicators_path"]["path"])))
    classification_frame = pd.read_csv(Path(input_metadata["classification_map_path"]["path"]))
    classification_by_code = _index_frame_by_code(classification_frame)
    industry_path_key = next(key for key in input_metadata if key in INDUSTRY_INPUT_KEYS)
    industry_frame = pd.read_csv(Path(input_metadata[industry_path_key]["path"]))
    minute_frame = (pd.read_parquet(Path(input_metadata["intraday_minutes_path"]["path"]))
                    if "intraday_minutes_path" in input_metadata else pd.DataFrame())
    minute_frame = _normalize_frame_code_column(minute_frame)
    market_breadth = _read_json(Path(input_metadata["market_breadth_path"]["path"]))
    data_audit = _read_json(Path(input_metadata["data_audit_path"]["path"]))
    handoff_observed_at = _resolve_handoff_observed_at(manifest)
    market = _build_market_context(market_breadth, data_audit, handoff_observed_at)
    market["session_phase"] = data_audit.get("session_phase")
    # Read the cached exchange calendar without a weekday fallback or network call.
    calendar_path = Path(__file__).resolve().parents[1] / "data/daily_runs/cache/trade_calendar.csv"
    trade_date = str(candidate_union.get("trade_date", ""))
    previous_date = None
    if calendar_path.is_file():
        calendar_dates = pd.read_csv(calendar_path, dtype=str)["trade_date"].dropna().tolist()
        earlier = [value for value in calendar_dates if value < trade_date]
        if trade_date in calendar_dates and earlier:
            previous_date = max(earlier)
    valid_daily_dates = {previous_date} if previous_date else set()
    if handoff_observed_at.astimezone(timezone(timedelta(hours=8))).hour >= 15:
        valid_daily_dates.add(trade_date)
    market["previous_trading_date"] = previous_date
    market["daily_input_issues"] = {}
    if "intraday_minutes_path" not in input_metadata:
        market["critical_ready"] = False
        market["minute_input_issue"] = manifest.get("_minute_input_issue", "minute_path_null")
        market["declared_minute_path"] = manifest.get("intraday_minutes_path")
    objective_by_code: dict[str, CandidateObjectiveData] = {}
    for raw_candidate in _to_native(candidate_union.get("candidates", [])) or []:
        candidate = _native_mapping(raw_candidate)
        code = _normalize_code(candidate.get("code", ""))
        pivot, pivot_source = _pick_named_pivot(candidate)
        code_minutes = minute_frame[minute_frame["code"] == code].copy() if "code" in minute_frame.columns else minute_frame.iloc[0:0].copy()
        if pivot is not None and not code_minutes.empty:
            code_minutes["named_pivot"] = pivot
        normalized_minutes = _normalize_minute_frame(code_minutes, code) if not code_minutes.empty else code_minutes.copy()
        minute_evidence = build_minute_evidence(normalized_minutes, code, decision_at)
        if pivot is not None:
            minute_evidence["named_pivot"] = pivot
            minute_evidence["pivot"] = pivot
            minute_evidence["pivot_source"] = pivot_source
        last_completed = None
        last_label = minute_evidence.get("last_completed_minute")
        if isinstance(last_label, str) and not normalized_minutes.empty:
            matches = normalized_minutes[normalized_minutes["_time_label"] == last_label]
            if not matches.empty:
                last_completed = {str(key): _to_native(value) for key, value in matches.iloc[-1].to_dict().items()}
        if last_completed is not None:
            last_completed["vwap"] = minute_evidence.get("vwap")
            last_completed["afternoon_vwap"] = minute_evidence.get("afternoon_vwap")
            last_completed["named_pivot"] = minute_evidence.get("named_pivot")
        industry = _industry_evidence_for_code(code, classification_by_code, industry_frame)
        snapshot = snapshot_by_code.get(code, {})
        executability = _build_executability(candidate, snapshot, minute_evidence)
        daily = daily_by_code.get(code, {})
        daily_date = daily.get("last_trade_date", daily.get("last_date"))
        if daily_date not in valid_daily_dates:
            market["daily_input_issues"][code] = "daily_date_invalid"
            daily = {}
        stale_audit = _native_mapping(data_audit.get("daily_staleness"))
        if (daily and daily_date == previous_date
                and code in stale_audit.get("stale_codes", [])
                and "failed_codes" in stale_audit and "unknown_quote_time_codes" in stale_audit
                and not stale_audit.get("unknown_quote_time_codes_truncated", False)
                and code not in stale_audit["failed_codes"]
                and code not in stale_audit["unknown_quote_time_codes"]):
            # This exemption is research-only. Original execution flags stay intact.
            daily = {**daily, "research_t_minus_one_cache": True}
        objective_by_code[code] = CandidateObjectiveData(
            snapshot=snapshot,
            daily=daily,
            industry=industry,
            minute=last_completed,
            minute_evidence=minute_evidence,
            executability=executability,
        )

    return {
        "trade_date": candidate_union.get("trade_date"),
        "observed_at": handoff_observed_at.isoformat(),
        "critical_ready": market["critical_ready"],
        "market": market,
        "objective_by_code": objective_by_code,
        "input_metadata": {
            "candidate_union": _file_metadata(Path(candidate_union["__path__"])),
            "declared_inputs": input_metadata,
        },
        "score_versions": {
            "candidate_union_schema_version": str(candidate_union.get("schema_version", SCHEMA_VERSION)),
            "execution_rule_version": EXECUTION_RULE_VERSION,
            "decision_rule_version": DECISION_RULE_VERSION,
        },
    }


def _validate_decision_payload(decision: Mapping[str, object]) -> None:
    research = decision.get("research_choices", [])
    if not isinstance(research, list) or len(research) > 3:
        raise ValueError("research choices must be a list of at most three")
    research_codes = []
    audited_codes = {row.get("code") for row in decision.get("audit_rows", []) if isinstance(row, Mapping)}
    for row in research:
        if (not isinstance(row, Mapping) or row.get("buyable") is not False
                or row.get("research_only") is not True or row.get("code") not in audited_codes):
            raise ValueError("research choices must remain non-buyable and traceable")
        if row.get("code") in research_codes or not str(row.get("code", "")).startswith(("60", "00")):
            raise ValueError("research choices must have unique mainboard codes")
        if (_coerce_float(row.get("price")) or 0) < PRODUCTION_MIN_PRICE:
            raise ValueError("research choices must respect production price range")
        if any(key in row for key in ("buy_low", "buy_high", "only_choose_one_eligible")):
            raise ValueError("research choices cannot carry an executable buy plan")
        research_codes.append(row["code"])
    if decision.get("research_first_choice") != (research_codes[0] if research_codes else None):
        raise ValueError("research first choice must match ranked research choices")
    required_keys = (
        "schema_version",
        "decision_rule_version",
        "execution_rule_version",
        "input_metadata",
        "summary",
        "audit_rows",
        "executable",
        "watch",
        "rejected",
        "shadow",
        "global_status",
        "only_choose_one",
    )
    for key in required_keys:
        if key not in decision:
            raise ValueError(f"decision payload missing required key: {key}")
    for key in ("schema_version", "decision_rule_version", "execution_rule_version"):
        value = decision.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"decision payload {key} must be a non-empty string")
    global_status = decision.get("global_status")
    if not isinstance(global_status, str) or global_status not in {"decision_ready", "data_not_ready"}:
        raise ValueError("decision payload global_status must be decision_ready or data_not_ready")
    input_metadata = _to_native(decision.get("input_metadata"))
    if not isinstance(input_metadata, Mapping):
        raise ValueError("decision input_metadata must be a mapping")
    input_metadata_mapping = _native_mapping(input_metadata)
    candidate_union_metadata = _native_mapping(input_metadata_mapping.get("candidate_union"))
    if not isinstance(candidate_union_metadata.get("path"), str) or not str(candidate_union_metadata["path"]).strip():
        raise ValueError("decision input_metadata.candidate_union.path must be a non-empty string")
    if not isinstance(candidate_union_metadata.get("sha256"), str) or not str(candidate_union_metadata["sha256"]).strip():
        raise ValueError("decision input_metadata.candidate_union.sha256 must be a non-empty string")
    declared_inputs = _to_native(input_metadata_mapping.get("declared_inputs"))
    if not isinstance(declared_inputs, Mapping) or not declared_inputs:
        raise ValueError("decision input_metadata.declared_inputs must be a non-empty mapping")
    for key, record in declared_inputs.items():
        record_mapping = _native_mapping(record)
        if not isinstance(record_mapping.get("path"), str) or not str(record_mapping["path"]).strip():
            raise ValueError(f"decision input_metadata.declared_inputs.{key}.path must be a non-empty string")
        if not isinstance(record_mapping.get("sha256"), str) or not str(record_mapping["sha256"]).strip():
            raise ValueError(f"decision input_metadata.declared_inputs.{key}.sha256 must be a non-empty string")

    summary = _to_native(decision.get("summary"))
    if not isinstance(summary, Mapping):
        raise ValueError("decision summary must be a mapping")
    for key in ("executable", "executable_exposed", "watch", "rejected", "shadow_count", "evaluated"):
        if _coerce_non_negative_int(summary.get(key)) is None:
            raise ValueError(f"decision summary.{key} must be a non-negative int")

    group_lengths: dict[str, int] = {}

    def _validate_row_group(group_name: str, *, enforce_shadow_false: bool = False) -> None:
        rows = decision.get(group_name)
        if not isinstance(rows, list):
            raise ValueError(f"decision {group_name} group must be a list")
        group_lengths[group_name] = len(rows)
        for row in rows:
            mapping = _native_mapping(row)
            code = str(mapping.get("code", "") or "")
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"{group_name} row code must be a 6-digit string")
            if not isinstance(mapping.get("decision"), str) or not str(mapping.get("decision")).strip():
                raise ValueError(f"{group_name} row decision must be a non-empty string")
            for flag in ("production_buyable", "buyable", "only_choose_one_eligible"):
                if not isinstance(mapping.get(flag), bool):
                    raise ValueError(f"{group_name} row {flag} must be bool")
            if not isinstance(mapping.get("reason_codes"), list):
                raise ValueError(f"{group_name} row reason_codes must be a list")
            if enforce_shadow_false:
                if mapping.get("production_buyable") is not False:
                    raise ValueError("shadow row must keep production_buyable=false")
                if mapping.get("buyable") is not False:
                    raise ValueError("shadow row must keep buyable=false")
                if mapping.get("only_choose_one_eligible") is not False:
                    raise ValueError("shadow row must keep only_choose_one_eligible=false")

    _validate_row_group("audit_rows")
    _validate_row_group("executable")
    _validate_row_group("watch")
    _validate_row_group("rejected")
    _validate_row_group("shadow", enforce_shadow_false=True)

    audit_rows = decision.get("audit_rows")
    if not isinstance(audit_rows, list):
        raise ValueError("decision audit_rows group must be a list")
    audit_by_code: dict[str, dict[str, object]] = {}
    for row in audit_rows:
        mapping = _native_mapping(row)
        code = str(mapping.get("code", "") or "")
        if code in audit_by_code:
            raise ValueError("decision audit_rows must have a unique code per row")
        audit_by_code[code] = mapping

    only_choose_one = decision.get("only_choose_one")
    if global_status == "data_not_ready":
        if decision.get("executable"):
            raise ValueError("data_not_ready decision executable group must be empty")
        if _coerce_non_negative_int(summary.get("executable")) != 0:
            raise ValueError("data_not_ready decision summary.executable must be zero")
        if _coerce_non_negative_int(summary.get("executable_exposed")) != 0:
            raise ValueError("data_not_ready decision summary.executable_exposed must be zero")
        if only_choose_one is not None:
            raise ValueError("data_not_ready decision must keep only_choose_one null")
        for row in audit_rows:
            mapping = _native_mapping(row)
            if mapping.get("entry_state") == EntryState.ENTRY_ACTIVE.value:
                raise ValueError("data_not_ready audit row cannot be entry_active")
            trajectory = _to_native(mapping.get("entry_state_trajectory"))
            if isinstance(trajectory, list):
                for transition in trajectory:
                    transition_mapping = _native_mapping(transition)
                    if EntryState.ENTRY_ACTIVE.value in {
                        transition_mapping.get("from_state"),
                        transition_mapping.get("to_state"),
                    }:
                        raise ValueError("data_not_ready audit row trajectory cannot contain entry_active")
            for flag in ("production_buyable", "buyable", "only_choose_one_eligible"):
                if mapping.get(flag) is True:
                    raise ValueError(f"data_not_ready audit row cannot set {flag}=true")

    expected_group_rows = {
        "executable": sorted(
            [row for row in audit_by_code.values() if row.get("decision") == "executable_candidate"],
            key=_sort_key,
            reverse=True,
        )[:3],
        "watch": [row for row in audit_by_code.values() if row.get("decision") == "conditional_watch"],
        "rejected": [
            row for row in audit_by_code.values() if row.get("decision") in {"reject", "data_insufficient"}
        ],
        "shadow": [
            row
            for row in audit_by_code.values()
            if row.get("decision") in {"shadow_watch", "shadow_reject"}
        ],
    }
    supported_decisions = {
        "executable_candidate",
        "conditional_watch",
        "reject",
        "data_insufficient",
        "shadow_watch",
        "shadow_reject",
    }
    for row in audit_by_code.values():
        row_decision = str(row.get("decision", "") or "")
        if row_decision not in supported_decisions:
            raise ValueError(f"decision audit_rows has unsupported decision classification: {row_decision}")

    for group_name, expected_rows in expected_group_rows.items():
        group_rows = [_native_mapping(row) for row in decision[group_name]]
        expected_codes = {str(row.get("code", "") or "") for row in expected_rows}
        actual_codes = [str(row.get("code", "") or "") for row in group_rows]
        if len(actual_codes) != len(set(actual_codes)) or set(actual_codes) != expected_codes:
            raise ValueError(
                f"decision {group_name} group must match audit_rows decision classification"
            )
        for group_row in group_rows:
            code = str(group_row.get("code", "") or "")
            audit_row = audit_by_code[code]
            if group_row != audit_row:
                differing_fields = sorted(
                    field
                    for field in group_row.keys() | audit_row.keys()
                    if group_row.get(field) != audit_row.get(field)
                )
                raise ValueError(
                    f"decision {group_name} row must match audit_rows field "
                    f"{differing_fields[0]} for code {code}"
                )

    executable_count = 0
    watch_count = 0
    rejected_count = 0
    shadow_count = 0
    for row in audit_rows:
        mapping = _native_mapping(row)
        row_decision = str(mapping.get("decision", "") or "")
        if row_decision == "executable_candidate":
            executable_count += 1
        elif row_decision == "conditional_watch":
            watch_count += 1
        elif row_decision in {"reject", "data_insufficient"}:
            rejected_count += 1
        elif row_decision in {"shadow_watch", "shadow_reject"}:
            shadow_count += 1
    if _coerce_non_negative_int(summary.get("executable")) != executable_count:
        raise ValueError("decision summary.executable must match audit_rows")
    if _coerce_non_negative_int(summary.get("executable_exposed")) != group_lengths["executable"]:
        raise ValueError("decision summary.executable_exposed must match executable group")
    if _coerce_non_negative_int(summary.get("watch")) != watch_count or watch_count != group_lengths["watch"]:
        raise ValueError("decision summary.watch must match watch group")
    if _coerce_non_negative_int(summary.get("rejected")) != rejected_count or rejected_count != group_lengths["rejected"]:
        raise ValueError("decision summary.rejected must match rejected group")
    if _coerce_non_negative_int(summary.get("shadow_count")) != shadow_count or shadow_count != group_lengths["shadow"]:
        raise ValueError("decision summary.shadow_count must match shadow group")
    if _coerce_non_negative_int(summary.get("evaluated")) != len(audit_rows):
        raise ValueError("decision summary.evaluated must match audit_rows")
    if only_choose_one is not None:
        code = str(only_choose_one)
        if len(code) != 6 or not code.isdigit():
            raise ValueError("decision only_choose_one must be null or a 6-digit code")
        eligible_rows = [
            _native_mapping(row)
            for row in audit_rows
            if _native_mapping(row).get("price_band") == "production"
            and _native_mapping(row).get("buyable") is True
            and _native_mapping(row).get("only_choose_one_eligible") is True
            and _native_mapping(row).get("decision") == "executable_candidate"
        ]
        eligible_codes = {str(row.get("code", "") or "") for row in eligible_rows}
        if code not in eligible_codes:
            raise ValueError("decision only_choose_one must reference an eligible executable candidate")
        expected_winner = choose_one(eligible_rows)
        if expected_winner is not None and code != expected_winner:
            raise ValueError("decision only_choose_one must match choose_one winner")


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return path


def _append_shadow_ledger(path: Path, decision: Mapping[str, object]) -> None:
    shadow_rows = decision.get("shadow", [])
    if not isinstance(shadow_rows, list) or not shadow_rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    trade_date = decision.get("trade_date")
    decision_at = decision.get("decision_at")
    score_versions = _native_mapping(decision.get("score_versions"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in shadow_rows:
            record = _native_mapping(row)
            payload = {
                "trade_date": trade_date,
                "decision_at": decision_at,
                "code": record.get("code"),
                "decision": record.get("decision"),
                "production_buyable": False,
                "score_versions": score_versions,
                "execution_rule_version": record.get("execution_rule_version", EXECUTION_RULE_VERSION),
                "audit_row": record,
            }
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def orchestrate_full_market_t1(
    *,
    data_root: str | Path,
    candidate_union_path: str | Path,
    decision_at: datetime,
    output_path: str | Path,
    handoff_path: str | Path | None = None,
) -> dict[str, object]:
    if decision_at.tzinfo is None:
        raise ValueError("decision_at must be timezone-aware")
    candidate_path = Path(candidate_union_path).expanduser().resolve()
    if not candidate_path.is_file():
        raise ValueError("candidate union path does not exist")
    candidate_union = _read_json(candidate_path)
    candidate_union["__path__"] = str(candidate_path)
    manifest, input_metadata = _load_handoff_manifest(data_root, handoff_path)
    _validate_contract(candidate_union, manifest)
    handoff = _prepare_handoff(
        candidate_union=candidate_union,
        manifest=manifest,
        input_metadata=input_metadata,
        decision_at=decision_at,
    )
    decision = build_full_market_decision(candidate_union, handoff, decision_at=decision_at)
    _validate_decision_payload(decision)
    target_path = Path(output_path).expanduser().resolve()
    _write_json_atomic(target_path, decision)
    _append_shadow_ledger(Path(data_root).expanduser().resolve() / "shadow" / "full_market_t1_shadow.jsonl", decision)
    return decision


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
        "lifecycle_not_confirmed",
        "lifecycle_data_insufficient",
    }


def _first_source_value(sources: Sequence[Mapping], field: str):
    for source in sources:
        if field in source and source[field] is not None:
            return source[field]
    return None


def _serialize_entry_history(machine: EntryStateMachine) -> list[dict[str, object]]:
    return [
        {
            "from_state": item.from_state.value,
            "to_state": item.to_state.value,
            "decision_id": item.decision_id,
            "reason_code": item.reason_code,
            "evidence": _to_native(item.evidence),
        }
        for item in machine.history
    ]


def _lifecycle_atr14_pct(
    candidate: Mapping[str, object],
    daily: Mapping[str, object],
    snapshot: Mapping[str, object],
    executability: Mapping[str, object],
) -> float | None:
    """Adapt persisted percent points to the lifecycle's decimal-ratio contract."""
    for source_name, source in (
        ("candidate", candidate),
        ("daily", daily),
        ("snapshot", snapshot),
        ("executability", executability),
    ):
        value = _coerce_float(source.get("atr14_pct"))
        if value is None:
            continue
        unit = str(source.get("atr14_pct_unit", "") or "").strip()
        if unit == ATR14_PCT_INPUT_UNIT or (source_name == "daily" and not unit):
            return value / 100.0
        if unit in {ATR14_PCT_LIFECYCLE_UNIT, "ratio"} or not unit:
            return value
        return None
    return None


def _evaluate_lifecycle(
    candidate: Mapping[str, object],
    objective: CandidateObjectiveData,
    market: Mapping[str, object],
) -> tuple[dict[str, object], EntryStateMachine]:
    candidate_row = _native_mapping(candidate)
    snapshot = _native_mapping(objective.snapshot)
    daily = _native_mapping(objective.daily)
    industry = _native_mapping(objective.industry)
    minute = _native_mapping(objective.minute_evidence)
    executability = _native_mapping(objective.executability)
    sources = (candidate_row, daily, snapshot, executability)
    code = _normalize_code(candidate_row.get("code", ""))
    decision_id = f"full_market_t1:{code}:{EXECUTION_RULE_VERSION}"
    machine = EntryStateMachine(code, "full_market_t1_v1", EXECUTION_RULE_VERSION)

    numeric_fields = (
        "ma11", "ma23", "rsi14", "rps20", "momentum_delta_1",
        "momentum_delta_3", "boll_position", "return_5d", "return_20d",
        "atr14", "atr14_pct", "ma20", "volume_ratio", "change_pct",
        "turnover_rate",
    )
    values = {field: _coerce_float(_first_source_value(sources, field)) for field in numeric_fields}
    values["atr14_pct"] = _lifecycle_atr14_pct(candidate_row, daily, snapshot, executability)
    missing = [field for field, value in values.items() if value is None]
    trend_aligned = _first_source_value(sources, "trend_aligned")
    industry_sync = industry.get("industry_sync")
    tail_tags_raw = candidate_row.get("tail_risk_tags")
    if isinstance(tail_tags_raw, str):
        tail_tags = {item for item in tail_tags_raw.split("|") if item}
    elif isinstance(tail_tags_raw, list):
        tail_tags = {str(item) for item in tail_tags_raw}
    else:
        tail_tags = set()
        missing.append("tail_risk_tags")
    if not isinstance(trend_aligned, bool):
        missing.append("trend_aligned")
    if not isinstance(industry_sync, bool):
        missing.append("industry_sync")

    risk_flags: dict[str, bool] = {}
    for field, tag in (
        ("repeated_upper_shadow", "repeated_upper_shadow"),
        ("long_upper_shadow", "long_upper_shadow"),
        ("industry_crowding", "industry_crowding"),
        ("limit_proximity", "limit_proximity"),
    ):
        explicit = _first_source_value((snapshot, candidate_row), field)
        risk_flags[field] = explicit if isinstance(explicit, bool) else tag in tail_tags

    if missing:
        missing = sorted(set(missing))
        machine.to_unassessed_to_data_insufficient(decision_id, {"missing_fields": missing})
        return {
            "strategy_channel": "unresolved",
            "lifecycle_channel": "unresolved",
            "channel_mapping_version": CHANNEL_MAPPING_VERSION,
            "channel_evidence": {"status": "data_insufficient", "missing_fields": missing, "results": []},
            "dual_axis": {"status": "not_evaluated", "reason_code": "lifecycle_data_insufficient"},
            "entry_state": machine.state.value,
            "entry_state_trajectory": _serialize_entry_history(machine),
            "reason_code": "lifecycle_data_insufficient",
        }, machine

    price = _coerce_float(candidate_row.get("price"))
    if price is None:
        price = _coerce_float(snapshot.get("price"))
    if price is None:
        machine.to_unassessed_to_data_insufficient(decision_id, {"missing_fields": ["price"]})
        return {
            "strategy_channel": "unresolved",
            "lifecycle_channel": "unresolved",
            "channel_mapping_version": CHANNEL_MAPPING_VERSION,
            "channel_evidence": {"status": "data_insufficient", "missing_fields": ["price"], "results": []},
            "dual_axis": {"status": "not_evaluated", "reason_code": "lifecycle_data_insufficient"},
            "entry_state": machine.state.value,
            "entry_state_trajectory": _serialize_entry_history(machine),
            "reason_code": "lifecycle_data_insufficient",
        }, machine

    channel_results = ChannelIdentifier.identify_all(
        adjusted_close=price,
        ma11=values["ma11"],
        ma23=values["ma23"],
        rsi14=values["rsi14"],
        rps20=values["rps20"],
        momentum_delta_1=values["momentum_delta_1"],
        momentum_delta_3=values["momentum_delta_3"],
        boll_position=values["boll_position"],
        return_20d=values["return_20d"],
        atr14=values["atr14"],
        atr14_pct=values["atr14_pct"],
        repeated_upper_shadow=risk_flags["repeated_upper_shadow"],
        full_day_vwap=_coerce_float(minute.get("vwap")),
        afternoon_avwap=_coerce_float(minute.get("afternoon_vwap")),
        pre_reclaim_low=_coerce_float(snapshot.get("low")),
        pullback_vwap_frozen=_coerce_float(minute.get("vwap")),
    )
    channel_rows = [
        {
            "channel": result.channel.value,
            "passed": result.passed,
            "production_buyable": result.production_buyable,
            "reason": result.reason,
            "evidence": _to_native(result.evidence),
        }
        for result in channel_results
    ]
    primary = next((result for result in channel_results if result.passed and result.production_buyable), None)
    if primary is None:
        machine.to_unassessed_to_rejected(decision_id, {"channel_results": channel_rows})
        return {
            "strategy_channel": "none",
            "lifecycle_channel": "none",
            "channel_mapping_version": CHANNEL_MAPPING_VERSION,
            "channel_evidence": {"status": "evaluated", "results": channel_rows},
            "dual_axis": {"status": "not_evaluated", "reason_code": "all_channels_hard_fail"},
            "entry_state": machine.state.value,
            "entry_state_trajectory": _serialize_entry_history(machine),
            "reason_code": "all_channels_hard_fail",
        }, machine

    lifecycle_channel = primary.channel.value
    strategy_channel = LIFECYCLE_TO_FROZEN_CHANNEL[lifecycle_channel]
    machine.to_unassessed_to_deep_watch(
        decision_id,
        {"strategy_channel": strategy_channel, "lifecycle_channel": lifecycle_channel},
    )
    protection_constructible = executability.get("protection_constructible") is True
    sell_zone_constructible = _coerce_float(candidate_row.get("sell1_low")) is not None
    if not sell_zone_constructible:
        sell_zone_constructible = _coerce_float(executability.get("sell1_low")) is not None
    dual = DualAxis.evaluate(
        trend_aligned=trend_aligned,
        rsi14=values["rsi14"],
        volume_ratio=values["volume_ratio"],
        change_pct=values["change_pct"],
        rps20=values["rps20"],
        return_5d=values["return_5d"],
        return_20d=values["return_20d"],
        industry_sync_pass=industry_sync,
        channel_type=primary.channel.value,
        price=price,
        ma20=values["ma20"],
        turnover_rate=values["turnover_rate"],
        atr14_pct=values["atr14_pct"],
        repeated_upper_shadow=risk_flags["repeated_upper_shadow"],
        long_upper_shadow=risk_flags["long_upper_shadow"],
        industry_crowding=risk_flags["industry_crowding"],
        limit_proximity=risk_flags["limit_proximity"],
        market_veto=(
            market.get("critical_ready") is False
            or (_coerce_float(market.get("advance_ratio")) or 0.0) < 0.35
        ),
        protection_constructible=protection_constructible,
        sell_zone_constructible=sell_zone_constructible,
    )
    dual_row = {
        "status": "evaluated",
        "opportunity_quality": dual.opportunity_quality.value,
        "tail_risk": dual.tail_risk.value,
        "decision": dual.decision.value,
        "op_score": dual.op_score,
        "op_detail": dual.op_detail,
        "tr_count": dual.tr_count,
        "tr_flags": dual.tr_flags,
        "reason_code": dual.reason_code,
    }
    lifecycle_reason = ""
    if dual.decision == DualAxisDecision.REJECT:
        machine.to_rejected(decision_id, dual.reason_code or "dual_axis_reject", {"dual_axis": dual_row})
        lifecycle_reason = dual.reason_code or "dual_axis_reject"
    else:
        machine.to_deep_watch_to_conditional_watch(decision_id, {"dual_axis": dual_row})
        if dual.decision == DualAxisDecision.CONDITIONAL_WATCH:
            lifecycle_reason = "dual_axis_conditional_watch"
        elif minute.get("confirmed") is True:
            machine.to_conditional_watch_to_confirmed_candidate(decision_id, {"minute_evidence": minute})
        else:
            lifecycle_reason = "lifecycle_not_confirmed"

    return {
        "strategy_channel": strategy_channel,
        "lifecycle_channel": lifecycle_channel,
        "channel_mapping_version": CHANNEL_MAPPING_VERSION,
        "channel_evidence": {"status": "evaluated", "results": channel_rows},
        "dual_axis": dual_row,
        "entry_state": machine.state.value,
        "entry_state_trajectory": _serialize_entry_history(machine),
        "reason_code": lifecycle_reason,
    }, machine


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
    lifecycle, entry_machine = _evaluate_lifecycle(candidate_row, objective_row, market_row)

    row = {
        **candidate_row,
        "code": code,
        "price_band": price_band,
        "price": price,
        "opportunity_score": round(opportunity_score, 2),
        "execution_score": execution_score,
        "execution_rule_version": EXECUTION_RULE_VERSION,
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
        "strategy_channel": lifecycle["strategy_channel"],
        "lifecycle_channel": lifecycle["lifecycle_channel"],
        "channel_mapping_version": lifecycle["channel_mapping_version"],
        "channel_evidence": lifecycle["channel_evidence"],
        "dual_axis": lifecycle["dual_axis"],
        "entry_state": lifecycle["entry_state"],
        "entry_state_trajectory": lifecycle["entry_state_trajectory"],
    }

    lifecycle_reason = str(lifecycle.get("reason_code", "") or "")
    if candidate_status != "ready":
        row["decision"] = "data_insufficient"
        row["reason_codes"] = reason_codes
        return row

    if price_band == "excluded":
        row["reason_codes"] = ["price_band_excluded"]
        return row

    gate_failures: list[str] = []
    if entry_machine.state != EntryState.CONFIRMED_CANDIDATE:
        gate_failures.append(lifecycle_reason or "lifecycle_not_confirmed")
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
        decision_id = f"full_market_t1:{code}:{EXECUTION_RULE_VERSION}"
        entry_machine.to_confirmed_candidate_to_plan_published(
            decision_id,
            {"price_band": price_band, "production_gates": "passed"},
        )
        entry_machine.to_plan_published_to_entry_active(
            decision_id,
            {"price": price, "minute_confirmation": True},
        )
        row["entry_state"] = entry_machine.state.value
        row["entry_state_trajectory"] = _serialize_entry_history(entry_machine)
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


def build_research_choices(rows, objectives, *, static_ready):
    """Rank static research separately; missing minutes never become an entry plan."""
    if not static_ready:
        return []
    choices = []
    for row in sorted(rows, key=lambda r: (
            r.get("buyable") is True, r.get("decision") == "conditional_watch",
            _coerce_float(r.get("opportunity_score")) or 0,
            _coerce_float(r.get("execution_score")) or 0, str(r.get("code", ""))), reverse=True):
        code = _normalize_code(row.get("code", ""))
        objective = _coerce_objective(objectives.get(code, {}))
        snapshot, daily = objective.snapshot, objective.daily
        if not snapshot or not daily or not objective.industry:
            continue
        price = _coerce_float(snapshot.get("price"))
        if price is None or classify_price_band(code, str(row.get("market", "")), price) != "production":
            continue
        if not code.startswith(("60", "00")) or (_coerce_float(row.get("opportunity_score")) or 0) < 55:
            continue
        if any(_coerce_float(daily.get(field)) is None for field in ("ma20", "rsi14", "rps20", "return_20d")):
            continue
        name = str(row.get("name", ""))
        if "ST" in name.upper() or "退" in name:
            continue
        if any(snapshot.get(flag) is True for flag in ("is_st", "is_suspended", "delist_risk")):
            continue
        if ((snapshot.get("is_untradable") is True
             or _native_mapping(row.get("executability")).get("is_untradable") is True)
                and daily.get("research_t_minus_one_cache") is not True):
            continue
        if _coerce_float(snapshot.get("change_pct")) is None or abs(float(snapshot["change_pct"])) >= 9.9:
            continue
        choices.append({
            "rank": len(choices) + 1, "code": code, "name": name, "price": price,
            "industry": objective.industry.get("industry", row.get("industry")),
            "research_channels": row.get("research_channels", []),
            "opportunity_score": row.get("opportunity_score"),
            "execution_score": row.get("execution_score"),
            "execution_status": row.get("decision"),
            "reason_codes": row.get("reason_codes", []),
            "candidate_reason": row.get("candidate_reason", []),
            "daily_reference_date": daily.get("last_trade_date", daily.get("last_date")),
            "research_t_minus_one_cache": daily.get("research_t_minus_one_cache", False),
            "research_only": True, "buyable": False,
        })
        if len(choices) == 3:
            break
    return choices


def build_full_market_decision(candidate_union: Mapping, handoff: Mapping, *, decision_at: datetime) -> dict[str, object]:
    candidate_union_row = _native_mapping(candidate_union)
    handoff_row = _native_mapping(handoff)
    market = _native_mapping(handoff_row.get("market")) or _native_mapping(candidate_union_row.get("market"))
    objective_by_code_source = (
        _native_mapping(handoff_row.get("objective_by_code"))
        or _native_mapping(candidate_union_row.get("objective_by_code"))
    )

    effective_critical_ready = not (
        handoff_row.get("critical_ready") is False or market.get("critical_ready") is False
    )
    static_ready = market.get("static_ready", effective_critical_ready) is True
    local_decision_at = decision_at.astimezone(timezone(timedelta(hours=8)))
    closed_session = local_decision_at.hour >= 15 or market.get("session_phase") == "post_close"
    observations = [candidate_union_row.get("observed_at"), handoff_row.get("observed_at")]
    snapshot_ages = [(decision_at - _parse_iso8601(value, label="observed_at")).total_seconds()
                     for value in observations if value]
    stale_snapshot = not snapshot_ages or any(age > 120 or age < -30 for age in snapshot_ages)
    if closed_session or stale_snapshot:
        effective_critical_ready = False
    lifecycle_market = {**market, "critical_ready": effective_critical_ready, "static_ready": static_ready}

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
        row = evaluate_candidate(candidate_row, objective, lifecycle_market)
        evaluated.append(row)

    global_status = "decision_ready" if effective_critical_ready else "data_not_ready"
    if global_status == "data_not_ready":
        for row in evaluated:
            row["production_buyable"] = False
            row["buyable"] = False
            row["only_choose_one_eligible"] = False
            _append_unique(row["reason_codes"], "global_data_not_ready")
            if closed_session:
                _append_unique(row["reason_codes"], "closed_session")
            elif stale_snapshot:
                _append_unique(row["reason_codes"], "stale_snapshot")
            if row.get("decision") == "executable_candidate":
                row["decision"] = "data_insufficient"

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
    research_choices = build_research_choices(evaluated, objective_by_code_source,
        static_ready=lifecycle_market.get("static_ready", effective_critical_ready) is True)

    return {
        "schema_version": candidate_union_row.get("schema_version", SCHEMA_VERSION),
        "decision_rule_version": DECISION_RULE_VERSION,
        "execution_rule_version": EXECUTION_RULE_VERSION,
        "decision_at": decision_at.isoformat(),
        "trade_date": candidate_union_row.get("trade_date") or handoff_row.get("trade_date"),
        "observed_at": candidate_union_row.get("observed_at") or handoff_row.get("observed_at"),
        "market": lifecycle_market,
        "global_status": global_status,
        "research_status": "ready" if static_ready else "data_not_ready",
        "execution_status": "closed_session" if closed_session else ("stale_snapshot" if stale_snapshot else (
            "evaluated" if effective_critical_ready else "data_insufficient")),
        "snapshot_age_seconds": max(snapshot_ages) if snapshot_ages else None,
        "pipeline_status": "completed",
        "only_choose_one": only_choose_one_code,
        "research_first_choice": research_choices[0]["code"] if research_choices else None,
        "research_choices": research_choices,
        "research_shortfall": max(0, 3 - len(research_choices)),
        "input_metadata": _native_mapping(handoff_row.get("input_metadata")),
        "score_versions": _native_mapping(handoff_row.get("score_versions")),
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

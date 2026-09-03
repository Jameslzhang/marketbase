from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

PRODUCTION_MIN_PRICE = 50.0
SHADOW_MIN_PRICE = 40.0
RESTRICTED_PREFIXES = ("300", "301", "688")
SCHEMA_VERSION = "1.0.0"


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

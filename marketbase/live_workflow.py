# -*- coding: utf-8 -*-
"""Three-market live acquisition, BSE fallback, and raw minute rows."""

from __future__ import annotations

from datetime import datetime, time as clock_time, timezone, timedelta
import json
import re
import time
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import requests


REFERENCE_FIELDS = (
    "volume_ratio",
    "turnover_rate",
    "pe_ratio",
    "pb_ratio",
    "total_mv",
    "circ_mv",
    "industry",
    "concepts",
)


def acquire_live_snapshot(
    *,
    primary_fetcher: Callable[[], pd.DataFrame] | None = None,
    reference_fetcher: Callable[[], pd.DataFrame] | None = None,
    now: datetime | None = None,
    min_rows: int = 1000,
    attempts: int = 3,
    required_markets: Iterable[str] = (),
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Fetch a complete live snapshot and merge slower reference attributes."""
    if primary_fetcher is None or reference_fetcher is None:
        from marketbase.snapshot import fetch_cn_snapshot

        primary_fetcher = primary_fetcher or (lambda: fetch_cn_snapshot("sina"))
        reference_fetcher = reference_fetcher or (
            lambda: fetch_cn_snapshot("efinance")
        )

    live = pd.DataFrame()
    primary_errors: list[str] = []
    used_attempts = 0
    for attempt in range(1, max(1, attempts) + 1):
        used_attempts = attempt
        try:
            fetched = primary_fetcher()
            if len(fetched) < min_rows:
                raise ValueError(
                    f"partial live snapshot rows={len(fetched)} required={min_rows}"
                )
            live = fetched
            break
        except Exception as exc:  # noqa: BLE001 - aggregate source diagnostics.
            primary_errors.append(str(exc))
    if live.empty:
        raise RuntimeError("live snapshot acquisition failed: " + "; ".join(primary_errors))

    reference = pd.DataFrame()
    reference_error = ""
    try:
        reference = reference_fetcher()
    except Exception as exc:  # noqa: BLE001 - live prices remain usable without reference.
        reference_error = str(exc)

    observed_at = now or datetime.now().astimezone()
    result = build_live_snapshot(live, reference, now=observed_at, min_rows=min_rows)
    validate_live_snapshot_freshness(result, now=observed_at, min_rows=min_rows)
    market_counts = _market_counts(result["code"])
    required = tuple(dict.fromkeys(str(item).strip().lower() for item in required_markets))
    missing_markets = [market for market in required if market_counts.get(market, 0) <= 0]
    if missing_markets:
        raise ValueError("live snapshot missing required markets: " + ", ".join(missing_markets))
    report: dict[str, object] = {
        "primary_source": "sina",
        "reference_source": str(
            reference.attrs.get("snapshot_source", "efinance")
        ),
        "primary_attempts": used_attempts,
        "primary_errors": primary_errors,
        "reference_error": reference_error,
        "live_rows": len(result),
        "reference_rows": len(reference),
        "market_counts": market_counts,
        "required_markets": list(required),
        "missing_markets": missing_markets,
        "generated_at": observed_at.isoformat(),
    }
    result.attrs["live_acquisition"] = report
    return result, report


def fetch_reference_snapshot_with_bse_fallback(
    cached_snapshot: pd.DataFrame | None,
    *,
    fetcher: Callable[[str], pd.DataFrame] | None = None,
    bse_fetcher: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    min_bse_rows: int = 300,
) -> pd.DataFrame:
    """Fetch reference fields, refreshing cached BSE symbols via Tencent on outage."""
    if fetcher is None:
        from marketbase.snapshot import fetch_cn_snapshot

        fetcher = fetch_cn_snapshot
    bse_fetcher = bse_fetcher or fetch_tencent_bse_snapshot
    errors: list[str] = []

    try:
        reference = fetcher("efinance")
        if _market_counts(reference["code"]).get("bj", 0) < min_bse_rows:
            raise ValueError("efinance reference missing BSE coverage")
        reference.attrs["snapshot_source"] = "efinance"
        return reference
    except Exception as exc:  # noqa: BLE001 - continue to independent providers.
        errors.append(f"efinance: {exc}")

    try:
        mainland = fetcher("em_datacenter")
    except Exception as exc:  # noqa: BLE001 - Sina still supplies live SH/SZ quotes.
        errors.append(f"em_datacenter: {exc}")
        mainland = pd.DataFrame()

    cached = cached_snapshot if cached_snapshot is not None else pd.DataFrame()
    if cached.empty or "code" not in cached.columns:
        raise RuntimeError(
            "BSE live fallback requires an existing snapshot code list; "
            + "; ".join(errors)
        )
    cached_codes = _normalize_codes(cached["code"])
    cached_bse = cached.loc[cached_codes.str.startswith(("4", "8", "9"))].copy()
    cached_bse["code"] = cached_codes.loc[cached_bse.index]
    if len(cached_bse) < min_bse_rows:
        raise RuntimeError(
            f"cached BSE universe rows={len(cached_bse)} required={min_bse_rows}; "
            + "; ".join(errors)
        )

    refreshed_bse = bse_fetcher(cached_bse)
    required_refresh_rows = max(min_bse_rows, int(len(cached_bse) * 0.98))
    if len(refreshed_bse) < required_refresh_rows:
        raise RuntimeError(
            f"Tencent BSE live rows={len(refreshed_bse)} required={required_refresh_rows}"
        )
    result = pd.concat([mainland, refreshed_bse], ignore_index=True, sort=False)
    result["code"] = _normalize_codes(result["code"])
    result = result.drop_duplicates("code", keep="last").reset_index(drop=True)
    result.attrs["snapshot_source"] = "em_datacenter+tencent_bse"
    result.attrs["source_errors"] = errors
    return result


def fetch_tencent_bse_snapshot(
    cached_bse: pd.DataFrame,
    *,
    batch_size: int = 60,
    attempts: int = 3,
    timeout: float = 15.0,
) -> pd.DataFrame:
    """Refresh a known BSE universe from Tencent without reusing stale prices."""
    if cached_bse.empty or "code" not in cached_bse.columns:
        return pd.DataFrame(columns=cached_bse.columns)
    base = cached_bse.copy()
    base = base.drop(
        columns=[
            column
            for column in (
                "source",
                "quote_time",
                "ticktime",
                "volume_ratio",
                "observed_at",
                "timestamp",
            )
            if column in base
        ]
    )
    base["code"] = _normalize_codes(base["code"])
    base = base.drop_duplicates("code", keep="last").set_index("code")
    codes = list(base.index)
    refreshed: list[dict[str, object]] = []
    batch_width = max(1, batch_size)

    for offset in range(0, len(codes), batch_width):
        batch = codes[offset : offset + batch_width]
        symbols = ",".join(f"bj{code}" for code in batch)
        last_error: Exception | None = None
        text = ""
        for attempt in range(1, max(1, attempts) + 1):
            try:
                response = requests.get(
                    "https://qt.gtimg.cn/q=" + symbols,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://gu.qq.com/",
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                text = response.content.decode("gb18030", errors="ignore")
                break
            except Exception as exc:  # noqa: BLE001 - retry a failed quote batch.
                last_error = exc
                if attempt < max(1, attempts):
                    time.sleep(0.2 * attempt)
        if not text:
            raise RuntimeError(f"Tencent BSE quote batch failed: {last_error}")

        for match in re.finditer(r'v_bj(\d{6})="([^"]*)";', text):
            code, body = match.groups()
            fields = body.split("~")
            price = _quote_float(fields, 3)
            if code not in base.index or price <= 0:
                continue
            quote_time = _quote_time(fields)
            row = base.loc[code].to_dict()
            row.update({
                "code": code,
                "symbol": f"bj{code}",
                "name": fields[1].strip() if len(fields) > 1 else "",
                "price": price,
                "settlement": _quote_float(fields, 4),
                "open": _quote_float(fields, 5),
                "pricechange": _quote_float(fields, 31),
                "change_pct": _quote_float(fields, 32),
                "high": _quote_float(fields, 33),
                "low": _quote_float(fields, 34),
                "volume": _quote_float(fields, 36) * 100,  # Tencent BSE returns 手, convert to 股
                "amount": _quote_amount(fields),
                "turnover_rate": _quote_float(fields, 38),
                "volume_ratio": float("nan"),
                "quote_time": quote_time,
                "ticktime": quote_time,
                "source": "tencent_bse",
            })
            refreshed.append(row)

    result = pd.DataFrame(refreshed)
    result.attrs["snapshot_source"] = "tencent_bse"
    return result


def _quote_float(fields: list[str], index: int) -> float:
    if index >= len(fields):
        return 0.0
    try:
        return float(fields[index])
    except (TypeError, ValueError):
        return 0.0


def _quote_amount(fields: list[str]) -> float:
    if len(fields) > 35:
        parts = fields[35].split("/")
        if len(parts) >= 3:
            try:
                return float(parts[2])
            except ValueError:
                pass
    return _quote_float(fields, 37) * 10_000.0


def _quote_time(fields: list[str]) -> str:
    if len(fields) <= 30:
        return ""
    value = fields[30].strip()
    if len(value) != 14 or not value.isdigit():
        return value
    # Preserve full YYYYMMDDHHMMSS as ISO-like timestamp
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}T{value[8:10]}:{value[10:12]}:{value[12:14]}"


def build_live_snapshot(
    live_df: pd.DataFrame,
    reference_df: pd.DataFrame | None = None,
    *,
    now: datetime | None = None,
    min_rows: int = 1000,
) -> pd.DataFrame:
    """Keep live quote fields authoritative and fill slower reference fields."""
    if live_df.empty or len(live_df) < min_rows:
        raise ValueError(
            f"live snapshot rows {len(live_df)} below required minimum {min_rows}"
        )
    if "code" not in live_df.columns:
        raise ValueError("live snapshot missing code")

    result = live_df.copy()
    result["code"] = _normalize_codes(result["code"])
    result = result.loc[result["code"].ne("")].drop_duplicates("code", keep="last")
    reference = reference_df if reference_df is not None else pd.DataFrame()
    if not reference.empty and "code" in reference.columns:
        supplement = reference.copy()
        supplement["code"] = _normalize_codes(supplement["code"])
        supplement = supplement.loc[supplement["code"].ne("")].drop_duplicates(
            "code", keep="last"
        )
        supplement_indexed = supplement.set_index("code")
        for field in REFERENCE_FIELDS:
            if field not in supplement_indexed.columns:
                continue
            incoming = result["code"].map(supplement_indexed[field])
            if field not in result.columns:
                result[field] = incoming
                continue
            missing = result[field].isna()
            if result[field].dtype == "object":
                missing |= result[field].astype(str).str.strip().isin({"", "nan", "None"})
            result.loc[missing, field] = incoming.loc[missing].to_numpy()

        reference_only = supplement.loc[~supplement["code"].isin(result["code"])].copy()
        if not reference_only.empty:
            reference_only = reference_only.reindex(columns=result.columns)
            result = pd.concat([result, reference_only], ignore_index=True, sort=False)

    result["symbol"] = result["code"].map(_market_symbol_from_code)

    observed_at = now or datetime.now().astimezone()
    result["timestamp"] = observed_at.isoformat()
    result.attrs["snapshot_source"] = "live_merged"
    result.attrs["generated_at"] = observed_at.isoformat()
    result.attrs["fallback_used"] = False
    return result.reset_index(drop=True)


def _market_symbol_from_code(code: object) -> str:
    text = str(code).strip().zfill(6)
    if text.startswith("6"):
        return f"sh{text}"
    if text.startswith(("0", "3")):
        return f"sz{text}"
    if text.startswith(("4", "8", "9")):
        return f"bj{text}"
    return text


def _market_counts(codes: pd.Series) -> dict[str, int]:
    markets = codes.map(_market_symbol_from_code).str[:2]
    return {
        market: int((markets == market).sum())
        for market in ("sh", "sz", "bj")
    }


def validate_live_snapshot_freshness(
    frame: pd.DataFrame,
    *,
    now: datetime | None = None,
    min_rows: int = 1000,
    max_age_minutes: float = 15.0,
) -> None:
    """Reject stale or partial data while the A-share session is in progress."""
    observed_at = now or datetime.now().astimezone()
    if len(frame) < min_rows:
        raise ValueError(
            f"live snapshot rows {len(frame)} below required minimum {min_rows}"
        )
    if "timestamp" not in frame.columns:
        raise ValueError("live snapshot missing timestamp")
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True).dropna()
    if timestamps.empty:
        raise ValueError("live snapshot has no valid timestamp")
    newest = timestamps.max().to_pydatetime().astimezone(observed_at.tzinfo)
    if _is_cn_market_session(observed_at) and newest.date() != observed_at.date():
        raise ValueError("live snapshot is not from current trading date")
    age_minutes = (observed_at - newest).total_seconds() / 60.0
    if _is_cn_market_session(observed_at) and age_minutes > max_age_minutes:
        raise ValueError(
            f"live snapshot age {age_minutes:.1f} minutes exceeds {max_age_minutes:.1f}"
        )




def fetch_tencent_minute_rows(code: str, *, timeout: float = 15.0) -> list[str]:
    symbol = _tencent_minute_symbol(code)
    response = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
        params={"code": symbol},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    stock = (payload.get("data") or {}).get(symbol) or {}
    data = stock.get("data") or {}
    rows = data.get("data") or []
    if not isinstance(rows, list):
        raise ValueError(f"malformed minute response for {code}")
    return [str(item) for item in rows]


def _tencent_minute_symbol(code: str) -> str:
    normalized = str(code).strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise ValueError(f"unsupported A-share code: {code}")
    if normalized.startswith("6"):
        return f"sh{normalized}"
    if normalized.startswith(("0", "3")):
        return f"sz{normalized}"
    if normalized.startswith(("4", "8", "9")):
        return f"bj{normalized}"
    raise ValueError(f"unsupported A-share code: {code}")














def _normalize_codes(values: pd.Series) -> pd.Series:
    text = values.astype("string").fillna("").str.strip().str.replace(r"\.0$", "", regex=True)
    numeric = text.str.fullmatch(r"\d+")
    text.loc[numeric] = text.loc[numeric].str.zfill(6).str[-6:]
    return text.astype(str)


def _is_cn_market_session(value: datetime) -> bool:
    from marketbase.calendar import is_cn_market_session
    return is_cn_market_session(value)


# ── BSE (北交所) standalone snapshot collection ──────────────────────────

_BSE_CODE_PATTERN = re.compile(r"^(4|8|9)\d{5}$")
_MIN_BSE_ROWS = 200
_BSE_UNIVERSE_CACHE = "bse_universe.json"


def collect_bse_snapshot(
    *,
    cache_dir: str | Path,
    observed_at: datetime | None = None,
    bse_codes: list[str] | None = None,
    timeout: float = 15.0,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Collect BSE snapshot independently of SH/SZ collection.

    Returns (bse_frame, audit) where audit contains:
      bj_expected, bj_actual, bj_missing, source, errors
    """
    root = Path(cache_dir)
    observed = (observed_at or datetime.now().astimezone())
    if observed.tzinfo is None:
        observed = observed.astimezone()

    codes = bse_codes or _load_bse_universe(root)
    audit: dict[str, object] = {
        "bj_expected": len(codes),
        "bj_actual": 0,
        "bj_missing": 0,
        "source": "",
        "errors": [],
        "generated_at": observed.isoformat(),
    }

    if not codes:
        audit["errors"].append("no BSE code list available")
        return pd.DataFrame(), audit

    # 1. Try Tencent
    try:
        frame, tencent_errors = _fetch_tencent_bse(codes, timeout=timeout)
        if len(frame) >= _MIN_BSE_ROWS:
            audit["bj_actual"] = len(frame)
            audit["source"] = "tencent"
            audit["errors"] = tencent_errors
            _persist_bse_universe(root, frame)
            return frame, audit
        audit["errors"].extend(tencent_errors)
        audit["errors"].append(f"tencent BSE rows={len(frame)} below min={_MIN_BSE_ROWS}")
    except Exception as exc:
        audit["errors"].append(f"tencent: {exc}")

    # 2. Try Sina
    try:
        frame, sina_errors = _fetch_sina_bse(codes, timeout=timeout)
        if len(frame) >= _MIN_BSE_ROWS:
            audit["bj_actual"] = len(frame)
            audit["source"] = "sina_bse"
            audit["errors"] = sina_errors
            _persist_bse_universe(root, frame)
            return frame, audit
        audit["errors"].extend(sina_errors)
        audit["errors"].append("sina_bse_untested: insufficient rows")
    except Exception as exc:
        audit["errors"].append(f"sina_bse_untested: {exc}")

    # 3. Fall back to last cached BSE snapshot
    cached = _load_cached_bse_snapshot(root)
    if not cached.empty:
        audit["bj_actual"] = len(cached)
        audit["source"] = "bse_cache_stale"
        audit["bj_missing"] = max(0, len(codes) - len(cached))
        audit["errors"].append("using stale BSE cache")
        return cached, audit

    audit["bj_missing"] = len(codes)
    audit["errors"].append("BSE collection failed: no source available")
    return pd.DataFrame(), audit


def _load_bse_universe(cache_dir: Path) -> list[str]:
    path = cache_dir / _BSE_UNIVERSE_CACHE
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        codes = payload.get("codes", [])
        return [c for c in codes if isinstance(c, str) and _BSE_CODE_PATTERN.fullmatch(c)]
    except (OSError, json.JSONDecodeError):
        return []


def _persist_bse_universe(cache_dir: Path, frame: pd.DataFrame) -> None:
    if frame.empty or "code" not in frame.columns:
        return
    codes = sorted(
        c for c in frame["code"].astype(str).str.strip()
        if _BSE_CODE_PATTERN.fullmatch(c)
    )
    if not codes:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(codes),
        "codes": codes,
    }
    (cache_dir / _BSE_UNIVERSE_CACHE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_cached_bse_snapshot(cache_dir: Path) -> pd.DataFrame:
    snapshot_path = cache_dir / "market_snapshot.json"
    if not snapshot_path.is_file():
        return pd.DataFrame()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
    except (OSError, json.JSONDecodeError):
        return pd.DataFrame()
    if not isinstance(rows, list):
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if "code" not in frame.columns:
        return pd.DataFrame()
    codes = frame["code"].astype(str).str.strip()
    bse_mask = codes.str.fullmatch(_BSE_CODE_PATTERN.pattern, na=False)
    return frame.loc[bse_mask].copy()


def _fetch_tencent_bse(
    codes: list[str],
    *,
    batch_size: int = 60,
    attempts: int = 3,
    timeout: float = 15.0,
) -> tuple[pd.DataFrame, list[str]]:
    errors: list[str] = []
    refreshed: list[dict[str, object]] = []

    for offset in range(0, len(codes), batch_size):
        batch = codes[offset : offset + batch_size]
        symbols = ",".join(f"bj{code}" for code in batch)
        text = ""
        last_error = ""
        for attempt in range(1, max(1, attempts) + 1):
            try:
                response = requests.get(
                    "https://qt.gtimg.cn/q=" + symbols,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                    timeout=timeout,
                )
                response.raise_for_status()
                text = response.content.decode("gb18030", errors="ignore")
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt < max(1, attempts):
                    time.sleep(0.2 * attempt)
        if not text:
            errors.append(f"batch {offset}: {last_error}")
            continue

        for match in re.finditer(r'v_bj(\d{6})="([^"]*)";', text):
            code, body = match.groups()
            fields = body.split("~")
            price = _quote_float(fields, 3)
            if price <= 0:
                continue
            refreshed.append({
                "code": code,
                "name": fields[1].strip() if len(fields) > 1 else "",
                "price": price,
                "change_pct": _quote_float(fields, 32),
                "volume": _quote_float(fields, 36) * 100,  # Tencent BSE returns 手, convert to 股
                "amount": _quote_amount(fields),
                "turnover_rate": _quote_float(fields, 38),
                "volume_ratio": float("nan"),
                "quote_time": _quote_time(fields),
                "source": "tencent_bse",
                "market": "bj",
            })

    if not refreshed:
        return pd.DataFrame(), errors
    frame = pd.DataFrame(refreshed)
    frame = frame.drop_duplicates("code", keep="last").reset_index(drop=True)
    return frame, errors


def _fetch_sina_bse(
    codes: list[str],
    *,
    timeout: float = 15.0,
) -> tuple[pd.DataFrame, list[str]]:
    errors: list[str] = []
    refreshed: list[dict[str, object]] = []
    batch_size = 50

    for offset in range(0, len(codes), batch_size):
        batch = codes[offset : offset + batch_size]
        symbols = ",".join(f"bj{code}" for code in batch)
        try:
            response = requests.get(
                "https://hq.sinajs.cn/list=" + symbols,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn/",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            text = response.content.decode("gb18030", errors="ignore")
        except Exception as exc:
            errors.append(f"sina batch {offset}: {exc}")
            continue

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.match(r'var hq_str_bj(\d{6})="([^"]*)"', line)
            if not match:
                continue
            code, body = match.groups()
            fields = body.split(",")
            if len(fields) < 3:
                continue
            price = _quote_float(fields, 3)
            if price <= 0:
                continue
            refreshed.append({
                "code": code,
                "name": fields[0].strip() if fields else "",
                "price": price,
                "change_pct": 0.0 if len(fields) <= 4 else _pct_from_sina(fields),
                "volume": _quote_float(fields, 8) * 100,  # Sina BSE returns 手, convert to 股
                "amount": _quote_float(fields, 9),
                "turnover_rate": _quote_float(fields, 10) if len(fields) > 10 else 0.0,
                "volume_ratio": float("nan"),
                "quote_time": "",
                "source": "sina_bse",
                "market": "bj",
            })

    if not refreshed:
        return pd.DataFrame(), errors
    frame = pd.DataFrame(refreshed)
    frame = frame.drop_duplicates("code", keep="last").reset_index(drop=True)
    return frame, errors


def _pct_from_sina(fields: list[str]) -> float:
    return _quote_float(fields, 4)

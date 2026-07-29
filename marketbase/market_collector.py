"""客观实时行情快照采集."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time

import pandas as pd

from marketbase.data_audit import _neutralize_error, audit_market_snapshot
from marketbase.live_workflow import (
    acquire_live_snapshot,
    collect_bse_snapshot,
    fetch_reference_snapshot_with_bse_fallback,
)
from marketbase.snapshot import fetch_cn_snapshot


OUTPUT_FIELDS = (
    "code",
    "name",
    "market",
    "price",
    "pre_close",
    "open",
    "high",
    "low",
    "change_pct",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "total_mv",
    "circ_mv",
    "pe_ratio",
    "pb_ratio",
    "quote_time",
    "observed_at",
    "source",
    "industry",
    "concepts",
)
_NUMERIC_FIELDS = (
    "price",
    "pre_close",
    "open",
    "high",
    "low",
    "change_pct",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "total_mv",
    "circ_mv",
    "pe_ratio",
    "pb_ratio",
)
_TEXT_FIELDS = ("name", "industry", "concepts")
_MARKET_ORDER = {"sh": 0, "sz": 1, "bj": 2}


@dataclass(frozen=True)
class MarketCollectionResult:
    """标准化快照及其客观采集证据."""

    frame: pd.DataFrame
    audit: dict[str, object]
    report: dict[str, object]
    cache_path: Path


def collect_market_snapshot(
    *,
    cache_path: str | Path,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
    primary_fetcher: Callable[[], pd.DataFrame] | None = None,
    reference_fetcher: Callable[[], pd.DataFrame] | None = None,
    min_rows: int = 1000,
) -> MarketCollectionResult:
    """先采集沪深市场，再独立采集北交所。北交所失败不阻断沪深输出."""
    observed_at = now or datetime.now().astimezone()
    if observed_at.tzinfo is None:
        observed_at = observed_at.astimezone()
    destination = Path(cache_path)
    cached_reference = _read_cached_rows(destination)
    captured: dict[str, pd.DataFrame] = {}

    # --- SH/SZ primary + reference (existing pipeline without BSE) ---
    def load_primary() -> pd.DataFrame:
        frame = primary_fetcher() if primary_fetcher is not None else fetch_cn_snapshot("sina")
        captured["primary"] = frame
        return frame

    def load_reference() -> pd.DataFrame:
        if reference_fetcher is not None:
            frame = reference_fetcher()
        else:
            frame = fetch_reference_snapshot_with_bse_fallback(cached_reference)
        captured["reference"] = frame
        return frame

    _emit(progress, observed_at, "开始获取沪深行情快照")
    frame, acquisition = acquire_live_snapshot(
        primary_fetcher=load_primary,
        reference_fetcher=load_reference,
        now=observed_at,
        min_rows=min_rows,
        required_markets=("sh", "sz") if primary_fetcher is None else (),  # only enforce for default flow
    )
    shsz_result = _normalize_output(
        frame,
        observed_at=observed_at,
        primary=captured.get("primary", pd.DataFrame()),
        reference=captured.get("reference", pd.DataFrame()),
        primary_source=str(acquisition["primary_source"]),
        reference_source=str(acquisition["reference_source"]),
    )

    # --- BSE: independent collection ---
    _emit(progress, observed_at, "开始获取北交所行情")
    bse_frame = pd.DataFrame()
    bse_audit: dict[str, object] = {}
    # Extract BSE codes from the SH/SZ reference snapshot to seed the collector
    bse_codes = _extract_bse_codes(captured.get("reference", pd.DataFrame()))
    if not bse_codes:
        bse_codes = _extract_bse_codes(shsz_result)
    try:
        bse_frame, bse_audit = collect_bse_snapshot(
            cache_dir=destination.parent,
            observed_at=observed_at,
            bse_codes=bse_codes if bse_codes else None,
        )
        _emit(progress, observed_at,
              f"北交所覆盖 {bse_audit['bj_actual']}/{bse_audit['bj_expected']} "
              f"来源 {bse_audit['source']}")
        for err in bse_audit.get("errors", []):
            _emit(progress, observed_at, f"北交所 {err}")
    except Exception as exc:
        _emit(progress, observed_at, f"北交所采集失败: {_neutralize_error(exc)}")
        bse_audit = {"bj_expected": 0, "bj_actual": 0, "bj_missing": 0, "source": "", "errors": [str(exc)]}

    # --- Merge SH/SZ + BSE ---
    merged = _merge_shsz_bse(shsz_result, bse_frame, observed_at)
    if merged.empty:
        raise RuntimeError("live snapshot contains no usable market rows")

    # --- Preserve stocks from previous snapshot that disappeared (suspended / zero-volume) ---
    suspended_codes: list[str] = []
    if not cached_reference.empty and "code" in cached_reference.columns:
        prev_codes = set(cached_reference["code"].astype(str).str.strip().str.zfill(6))
        curr_codes = set(merged["code"].astype(str).str.strip().str.zfill(6))
        missing_codes = prev_codes - curr_codes
        if missing_codes:
            missing_rows = cached_reference.loc[
                cached_reference["code"].astype(str).str.strip().str.zfill(6).isin(missing_codes)
            ].copy()
            missing_rows["code"] = missing_rows["code"].astype(str).str.strip().str.zfill(6)
            missing_rows["observed_at"] = observed_at.isoformat()
            for field in _NUMERIC_FIELDS:
                if field not in missing_rows.columns:
                    missing_rows[field] = None
            missing_rows["volume"] = 0.0
            missing_rows["quote_time"] = ""
            missing_rows["source"] = missing_rows.get("source", "suspended")
            if "market" not in missing_rows.columns:
                missing_rows["market"] = missing_rows["code"].map(_market_for_code)
            for field in OUTPUT_FIELDS:
                if field not in missing_rows.columns:
                    missing_rows[field] = None
            missing_rows = missing_rows.loc[:, OUTPUT_FIELDS]
            merged = pd.concat([merged, missing_rows], ignore_index=True, sort=False)
            merged = merged.drop_duplicates("code", keep="first").reset_index(drop=True)
            suspended_codes = sorted(missing_codes)
            _emit(progress, observed_at, f"保留停牌/无成交股票 {len(suspended_codes)} 只")

    provider_errors = _provider_errors(acquisition, captured.get("reference"))
    provider_errors.extend(str(e) for e in bse_audit.get("errors", []) if str(e))
    audit = audit_market_snapshot(
        merged,
        observed_at=observed_at,
        provider_errors=provider_errors,
        raw_frame=_audit_evidence_frame(
            captured.get("primary", pd.DataFrame()),
            captured.get("reference", pd.DataFrame()),
            merged,
        ),
    )
    audit["bse_audit"] = bse_audit
    audit["suspended_codes"] = suspended_codes

    report = {
        key: value
        for key, value in acquisition.items()
        if key not in {"primary_errors", "reference_error"}
    }
    report.update(
        {
            "output_rows": len(merged),
            "market_counts": audit["market_counts"],
            "coverage_gaps": audit["coverage_gaps"],
            "provider_errors": audit["provider_errors"],
            "bse_audit": bse_audit,
            "suspended_codes": suspended_codes,
        }
    )
    _emit(progress, observed_at, f"获取到 {len(merged)} 条行情")
    _emit(progress, observed_at, f"市场覆盖 {_coverage_text(audit['market_counts'])}")
    if report["reference_source"] != "efinance":
        _emit(progress, observed_at, f"参考数据源 {report['reference_source']}")
    for error in audit["provider_errors"]:
        _emit(progress, observed_at, f"数据源异常 {error}")

    try:
        _write_cache(destination, observed_at, merged, report, audit)
        report["cache_written"] = True
    except OSError as exc:
        report["cache_written"] = False
        report["cache_error"] = _neutralize_error(exc)
        _emit(progress, observed_at, "缓存写入失败")

    return MarketCollectionResult(
        frame=merged,
        audit=audit,
        report=report,
        cache_path=destination,
    )


def _normalize_output(
    frame: pd.DataFrame,
    *,
    observed_at: datetime,
    primary: pd.DataFrame,
    reference: pd.DataFrame,
    primary_source: str,
    reference_source: str,
) -> pd.DataFrame:
    output = frame.copy()
    output["code"] = _codes(output)
    output = output.loc[output["code"].str.fullmatch(r"\d{6}", na=False)].copy()
    output["market"] = output["code"].map(_market_for_code)
    output = output.loc[output["market"].notna()].copy()
    output = output.drop_duplicates("code", keep="last")
    output["name"] = _text_column(output, "name")
    for field in ("industry", "concepts"):
        output[field] = _text_column(output, field)
    for field in _NUMERIC_FIELDS:
        output[field] = pd.to_numeric(output.get(field), errors="coerce")
    output["quote_time"] = _quote_time_column(output)
    output["observed_at"] = observed_at.isoformat()
    output["source"] = _source_column(
        output,
        primary=primary,
        reference=reference,
        primary_source=primary_source,
        reference_source=reference_source,
    )
    output["_market_order"] = output["market"].map(_MARKET_ORDER)
    output = output.sort_values(["_market_order", "code"], kind="stable")
    return output.loc[:, OUTPUT_FIELDS].reset_index(drop=True)


def _codes(frame: pd.DataFrame) -> pd.Series:
    values = frame.get("code", pd.Series("", index=frame.index))

    def normalize(value: object) -> str:
        if pd.isna(value):
            return ""
        text = str(value).strip()
        if text.isdigit() and len(text) <= 6:
            return text.zfill(6)
        return text

    return values.map(normalize)


def _market_for_code(code: str) -> str | None:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return None


def _text_column(frame: pd.DataFrame, field: str) -> pd.Series:
    values = frame.get(field, pd.Series("", index=frame.index))
    return values.map(lambda value: "" if pd.isna(value) else str(value).strip())


def _quote_time_column(frame: pd.DataFrame) -> pd.Series:
    if "quote_time" in frame:
        return _text_column(frame, "quote_time")
    if "ticktime" in frame:
        return _text_column(frame, "ticktime")
    return pd.Series("", index=frame.index, dtype="object")


def _source_column(
    frame: pd.DataFrame,
    *,
    primary: pd.DataFrame,
    reference: pd.DataFrame,
    primary_source: str,
    reference_source: str,
) -> pd.Series:
    sources = _text_column(frame, "source")
    primary_codes = set(_codes(primary).loc[lambda codes: codes.str.fullmatch(r"\d{6}")])
    reference_codes = _codes(reference)
    reference_values = _text_column(reference, "source")
    reference_map = dict(zip(reference_codes, reference_values, strict=False))
    source_values: list[str] = []
    for code, source in zip(frame["code"], sources, strict=False):
        if source:
            source_values.append(source)
        elif code in primary_codes:
            source_values.append(primary_source)
        else:
            source_values.append(reference_map.get(code) or reference_source)
    return pd.Series(source_values, index=frame.index, dtype="object")


def _provider_errors(
    acquisition: dict[str, object], reference: pd.DataFrame | None
) -> list[str]:
    errors = [str(error) for error in acquisition.get("primary_errors", [])]
    if acquisition.get("reference_error"):
        errors.append(f"reference: {acquisition['reference_error']}")
    if reference is not None:
        errors.extend(str(error) for error in reference.attrs.get("source_errors", []))
    return errors


def _audit_evidence_frame(
    primary: pd.DataFrame, reference: pd.DataFrame, output: pd.DataFrame
) -> pd.DataFrame:
    evidence = output.copy()
    duplicate_count = 0
    invalid_count = 0
    for upstream in (primary, reference):
        if upstream.empty:
            continue
        codes = _codes(upstream)
        valid = codes.str.fullmatch(r"\d{6}", na=False)
        duplicate_count += int(codes.loc[valid].duplicated().sum())
        invalid_count += int((~valid).sum())
    evidence.attrs["upstream_duplicate_code_count"] = duplicate_count
    evidence.attrs["upstream_invalid_code_count"] = invalid_count
    return evidence


def _merge_shsz_bse(
    shsz: pd.DataFrame, bse: pd.DataFrame, observed_at: datetime
) -> pd.DataFrame:
    """Merge mainstream SH/SZ with independently collected BSE rows."""
    if shsz.empty and bse.empty:
        return pd.DataFrame()
    if bse.empty or "code" not in bse.columns:
        return shsz
    # Normalize BSE columns to match OUTPUT_FIELDS
    bse_out = bse.copy()
    bse_out["code"] = bse_out["code"].astype(str).str.strip().str.zfill(6)
    bse_out["market"] = "bj"
    bse_out["observed_at"] = observed_at.isoformat()
    for field in _NUMERIC_FIELDS:
        if field not in bse_out.columns:
            bse_out[field] = None
    for field in OUTPUT_FIELDS:
        if field not in bse_out.columns:
            bse_out[field] = None
    bse_out = bse_out.loc[:, OUTPUT_FIELDS]
    result = pd.concat([shsz, bse_out], ignore_index=True, sort=False)
    result = result.drop_duplicates("code", keep="last").reset_index(drop=True)
    result["_market_order"] = result["market"].map(_MARKET_ORDER)
    result = result.sort_values(["_market_order", "code"], kind="stable")
    return result.reset_index(drop=True)


def _coverage_text(market_counts: object) -> str:
    counts = market_counts if isinstance(market_counts, dict) else {}
    return ",".join(f"{market}:{int(counts.get(market, 0))}" for market in ("sh", "sz", "bj"))


def _emit(
    progress: Callable[[str], None] | None, observed_at: datetime, message: str
) -> None:
    if progress is not None:
        progress(message)


def _extract_bse_codes(frame: pd.DataFrame) -> list[str]:
    """Extract BSE (北交所) codes from a snapshot frame."""
    if frame.empty or "code" not in frame.columns:
        return []
    codes = frame["code"].astype(str).str.strip().str.zfill(6)
    bse_mask = codes.str.match(r"^(4|8|9)\d{5}$", na=False)
    return sorted(codes.loc[bse_mask].unique().tolist())


def _read_cached_rows(path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
    except (OSError, ValueError, TypeError):
        return pd.DataFrame()
    return pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()


def _atomic_replace(temp_path: Path, target_path: Path, *, retries: int = 5, delay: float = 0.1) -> None:
    """Atomically replace *target_path* with *temp_path*, retrying on Windows lock errors."""
    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def _write_cache(
    path: Path,
    observed_at: datetime,
    frame: pd.DataFrame,
    report: dict[str, object],
    audit: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(frame.to_json(orient="records", force_ascii=False))
    payload = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "report": report,
        "audit": audit,
        "rows": rows,
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            temp_path = Path(handle.name)
        _atomic_replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

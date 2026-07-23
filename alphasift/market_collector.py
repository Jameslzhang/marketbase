"""Objective real-time market snapshot collection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

from alphasift.data_audit import _neutralize_error, audit_market_snapshot
from alphasift.live_workflow import (
    acquire_live_snapshot,
    fetch_reference_snapshot_with_bse_fallback,
)
from alphasift.snapshot import fetch_cn_snapshot


OUTPUT_FIELDS = (
    "code",
    "name",
    "market",
    "price",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "quote_time",
    "observed_at",
    "source",
)
_NUMERIC_FIELDS = (
    "price",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
)
_MARKET_ORDER = {"sh": 0, "sz": 1, "bj": 2}


@dataclass(frozen=True)
class MarketCollectionResult:
    """A normalized current snapshot plus its objective collection evidence."""

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
    """Collect a current three-market snapshot without interpreting the data."""
    observed_at = now or datetime.now().astimezone()
    if observed_at.tzinfo is None:
        observed_at = observed_at.astimezone()
    destination = Path(cache_path)
    cached_reference = _read_cached_rows(destination)
    captured: dict[str, pd.DataFrame] = {}

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

    _emit(progress, observed_at, "acquisition_started")
    frame, acquisition = acquire_live_snapshot(
        primary_fetcher=load_primary,
        reference_fetcher=load_reference,
        now=observed_at,
        min_rows=min_rows,
    )
    result = _normalize_output(
        frame,
        observed_at=observed_at,
        primary=captured.get("primary", pd.DataFrame()),
        reference=captured.get("reference", pd.DataFrame()),
        primary_source=str(acquisition["primary_source"]),
        reference_source=str(acquisition["reference_source"]),
    )
    if result.empty:
        raise RuntimeError("live snapshot contains no usable market rows")
    provider_errors = _provider_errors(acquisition, captured.get("reference"))
    audit = audit_market_snapshot(
        result,
        observed_at=observed_at,
        provider_errors=provider_errors,
        raw_frame=_audit_evidence_frame(
            captured.get("primary", pd.DataFrame()),
            captured.get("reference", pd.DataFrame()),
            result,
        ),
    )
    report = {
        key: value
        for key, value in acquisition.items()
        if key not in {"primary_errors", "reference_error"}
    }
    report.update(
        {
            "output_rows": len(result),
            "market_counts": audit["market_counts"],
            "coverage_gaps": audit["coverage_gaps"],
            "provider_errors": audit["provider_errors"],
        }
    )
    _emit(progress, observed_at, f"acquisition_rows={len(result)}")
    _emit(
        progress,
        observed_at,
        "market_coverage=" + _coverage_text(audit["market_counts"]),
    )
    if report["reference_source"] != "efinance":
        _emit(progress, observed_at, f"reference_source={report['reference_source']}")
    for error in audit["provider_errors"]:
        _emit(progress, observed_at, f"provider_error={error}")

    try:
        _write_cache(destination, observed_at, result, report, audit)
        report["cache_written"] = True
    except OSError as exc:
        report["cache_written"] = False
        report["cache_error"] = _neutralize_error(exc)
        _emit(progress, observed_at, "cache_write_error")

    return MarketCollectionResult(
        frame=result,
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


def _coverage_text(market_counts: object) -> str:
    counts = market_counts if isinstance(market_counts, dict) else {}
    return ",".join(f"{market}:{int(counts.get(market, 0))}" for market in ("sh", "sz", "bj"))


def _emit(
    progress: Callable[[str], None] | None, observed_at: datetime, message: str
) -> None:
    if progress is not None:
        progress(f"{observed_at.isoformat()} {message}")


def _read_cached_rows(path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
    except (OSError, ValueError, TypeError):
        return pd.DataFrame()
    return pd.DataFrame(rows) if isinstance(rows, list) else pd.DataFrame()


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
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

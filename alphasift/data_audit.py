"""Objective data-quality checks for live market snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import re

import pandas as pd


AUDITED_FIELDS = (
    "price",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "quote_time",
    "source",
)
_CODE_PATTERN = re.compile(r"\d{6}\Z")
_FORBIDDEN_ERROR_TERMS = re.compile(
    r"candidate|recommend|buy|sell|signal|score|rank|probability|"
    r"\u5019\u9009|\u63a8\u8350|\u4e70\u5165|\u5356\u51fa|\u4fe1\u53f7|"
    r"\u8bc4\u5206|\u6392\u540d|\u6982\u7387",
    re.IGNORECASE,
)
_STALE_AFTER = timedelta(minutes=15)


def audit_market_snapshot(
    frame: pd.DataFrame,
    *,
    observed_at: datetime,
    expected_markets: tuple[str, ...] = ("sh", "sz", "bj"),
    provider_errors: Iterable[str] = (),
    raw_frame: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Summarize observable snapshot quality without drawing market conclusions."""
    observed = _as_aware(observed_at)
    rows = frame.copy()
    total_rows = len(rows)
    codes = _column_as_text(rows, "code")
    row_valid_codes = codes.str.fullmatch(_CODE_PATTERN.pattern, na=False)
    audit_rows = raw_frame.copy() if raw_frame is not None else rows
    audit_codes = _column_as_text(audit_rows, "code")
    valid_codes = audit_codes.str.fullmatch(_CODE_PATTERN.pattern, na=False)
    valid_code_values = audit_codes.loc[valid_codes]
    duplicate_code_count = int(
        audit_rows.attrs.get(
            "upstream_duplicate_code_count", valid_code_values.duplicated().sum()
        )
    )
    invalid_code_count = int(
        audit_rows.attrs.get("upstream_invalid_code_count", (~valid_codes).sum())
    )
    markets = _column_as_text(rows, "market").str.lower()
    market_counts = {
        market: int(((markets == market) & row_valid_codes).sum())
        for market in expected_markets
    }
    field_non_null_counts = {
        field: int(_present(rows[field]).sum()) if field in rows else 0
        for field in AUDITED_FIELDS
    }
    field_coverage = {
        field: (count / total_rows if total_rows else 0.0)
        for field, count in field_non_null_counts.items()
    }
    price_values = _numeric_column(rows, "price")
    missing_price_count = int(price_values.isna().sum())
    stale_row_count = _stale_row_count(rows, observed)
    known_conditions = _known_conditions(rows, codes, price_values)
    coverage_gaps = [
        f"missing_market_{market}"
        for market, count in market_counts.items()
        if count == 0
    ]
    if duplicate_code_count:
        coverage_gaps.append(f"duplicate_code_count={duplicate_code_count}")
    if invalid_code_count:
        coverage_gaps.append(f"invalid_code_count={invalid_code_count}")
    if missing_price_count:
        coverage_gaps.append(f"missing_price_count={missing_price_count}")
    if stale_row_count:
        coverage_gaps.append(f"stale_row_count={stale_row_count}")
    normalized_errors = [_neutralize_error(error) for error in provider_errors if str(error)]
    if normalized_errors:
        coverage_gaps.append(f"provider_error_count={len(normalized_errors)}")

    quote_times = _column_as_text(rows, "quote_time")
    quote_times = quote_times.loc[_present(quote_times)]
    return {
        "observed_at": observed.isoformat(),
        "total_rows": int(total_rows),
        "unique_code_count": int(codes.loc[row_valid_codes].nunique()),
        "duplicate_code_count": duplicate_code_count,
        "invalid_code_count": invalid_code_count,
        "market_counts": market_counts,
        "field_non_null_counts": field_non_null_counts,
        "field_coverage": field_coverage,
        "latest_quote_time": quote_times.max() if not quote_times.empty else None,
        "stale_row_count": stale_row_count,
        "known_conditions": known_conditions,
        "coverage_gaps": coverage_gaps,
        "provider_errors": normalized_errors,
    }


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _column_as_text(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[name].map(lambda value: "" if pd.isna(value) else str(value).strip())


def _present(values: pd.Series) -> pd.Series:
    return values.notna() & ~values.astype(str).str.strip().str.lower().isin(
        {"", "nan", "none", "null"}
    )


def _numeric_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[name], errors="coerce")


def _stale_row_count(frame: pd.DataFrame, observed_at: datetime) -> int:
    if "observed_at" not in frame:
        return 0
    row_times = pd.to_datetime(frame["observed_at"], errors="coerce", utc=True)
    quote_times = _column_as_text(frame, "quote_time")
    for index, quote_time in quote_times.items():
        supplier_time = _supplier_quote_timestamp(quote_time, observed_at)
        if supplier_time is not None:
            row_times.loc[index] = supplier_time
    observed_utc = observed_at.astimezone(timezone.utc)
    ages = observed_utc - row_times
    return int((ages > _STALE_AFTER).sum())


def _supplier_quote_timestamp(value: str, observed_at: datetime) -> pd.Timestamp | None:
    text = value.strip()
    if not text:
        return None
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            quote_time = datetime.strptime(text, pattern).time()
        except ValueError:
            continue
        return pd.Timestamp(
            datetime.combine(observed_at.date(), quote_time, tzinfo=observed_at.tzinfo)
        ).tz_convert(timezone.utc)
    if len(text) == 14 and text.isdigit():
        try:
            timestamp = datetime.strptime(text, "%Y%m%d%H%M%S")
        except ValueError:
            return None
        return pd.Timestamp(timestamp.replace(tzinfo=observed_at.tzinfo)).tz_convert(
            timezone.utc
        )
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


def _known_conditions(
    frame: pd.DataFrame,
    codes: pd.Series,
    price_values: pd.Series,
) -> list[str]:
    volume_values = _numeric_column(frame, "volume")
    zero_volume = (volume_values == 0) & price_values.notna()
    conditions = [
        f"zero_volume_with_valid_price:{code}"
        for code in codes.loc[zero_volume]
        if _CODE_PATTERN.fullmatch(code)
    ]
    required = ("price", "volume", "amount", "turnover_rate", "quote_time", "source")
    other_fields_present = pd.Series(True, index=frame.index)
    for field in required:
        values = frame[field] if field in frame else pd.Series(None, index=frame.index)
        other_fields_present &= _present(values)
    volume_ratio_missing = ~_present(
        frame["volume_ratio"]
        if "volume_ratio" in frame
        else pd.Series(None, index=frame.index)
    )
    conditions.extend(
        f"volume_ratio_unavailable:{code}"
        for code in codes.loc[other_fields_present & volume_ratio_missing]
        if _CODE_PATTERN.fullmatch(code)
    )
    return conditions


def _neutralize_error(error: object) -> str:
    return _FORBIDDEN_ERROR_TERMS.sub("redacted", str(error).strip())

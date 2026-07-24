"""客观数据质量检查 —— 针对实时行情快照."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone, time as clock_time
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
_CN_MARKET_OPEN = clock_time(9, 30)
_CN_MARKET_CLOSE = clock_time(15, 0)
_CN_LUNCH_START = clock_time(11, 30)
_CN_LUNCH_END = clock_time(13, 0)


def _cn_trading_status(observed_at: datetime) -> str:
    """判断给定时间戳所属的交易状态。

    返回: 'live_session' | 'lunch_break' | 'post_close' | 'pre_open' | 'non_trading_day'
    """
    local = observed_at.astimezone(timezone(timedelta(hours=8)))
    if local.weekday() >= 5:
        return "non_trading_day"
    t = local.timetz().replace(tzinfo=None)
    if _CN_MARKET_OPEN <= t <= _CN_MARKET_CLOSE:
        if _CN_LUNCH_START <= t < _CN_LUNCH_END:
            return "lunch_break"
        return "live_session"
    if t > _CN_MARKET_CLOSE:
        return "post_close"
    return "pre_open"


def _quote_freshness(
    quote_time_str: str,
    observed_at: datetime,
    trading_status: str,
) -> tuple[str, str]:
    """分类单条行情数据的新鲜度。

    返回 (freshness_status, quote_status)。
    freshness_status: 'current' | 'stale' | 'unknown'
    quote_status: 'intraday' | 'final' | 'historical' | 'unknown'
    """
    if not quote_time_str or not quote_time_str.strip():
        return ("unknown", "unknown")

    observed_local = observed_at.astimezone(timezone(timedelta(hours=8)))
    quote_dt = _parse_quote_datetime(quote_time_str, observed_at)
    if quote_dt is None:
        return ("unknown", "unknown")

    quote_local = quote_dt.astimezone(timezone(timedelta(hours=8)))
    quote_date = quote_local.date()
    observed_date = observed_local.date()

    # Non-trading day: recent close data is valid historical
    if trading_status == "non_trading_day":
        if quote_date == observed_date or _is_recent_trading_day(quote_date, observed_date):
            return ("current", "historical")
        return ("stale", "stale")

    # Pre-open: today's pre-open data or yesterday's close
    if trading_status == "pre_open":
        if quote_date == observed_date:
            return ("current", "intraday")
        if _is_previous_trading_day(quote_date, observed_date):
            return ("current", "historical")
        return ("stale", "stale")

    # Lunch break: 11:30 quotes remain valid until 13:00
    if trading_status == "lunch_break":
        if quote_date == observed_date:
            return ("current", "intraday")
        if _is_previous_trading_day(quote_date, observed_date):
            return ("current", "historical")
        return ("stale", "stale")

    # Post-close: today's close data is final
    if trading_status == "post_close":
        if quote_date == observed_date:
            return ("current", "final")
        return ("stale", "stale")

    # Live session: check freshness
    if quote_date == observed_date:
        age = observed_at - quote_dt
        if age <= _STALE_AFTER:
            return ("current", "intraday")
        return ("stale", "intraday")

    # Different date during live session
    if _is_previous_trading_day(quote_date, observed_date):
        return ("stale", "historical")
    return ("stale", "stale")


def _parse_quote_datetime(value: str, observed_at: datetime) -> datetime | None:
    """Parse a quote time string into a timezone-aware datetime."""
    text = value.strip()
    if not text:
        return None
    tz = observed_at.tzinfo or timezone.utc
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            qt = datetime.strptime(text, pattern).time()
            return datetime.combine(observed_at.date(), qt, tzinfo=tz)
        except ValueError:
            continue
    if len(text) == 14 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=tz)
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed.to_pydatetime().replace(tzinfo=timezone.utc)


def _is_previous_trading_day(candidate: datetime.date, ref: datetime.date) -> bool:
    """Check if candidate is the most recent trading day before ref."""
    delta = (ref - candidate).days
    if delta <= 0:
        return False
    if delta == 1 and ref.weekday() == 0 and candidate.weekday() == 4:
        return True  # Friday → Monday
    if delta == 1 and ref.weekday() != 0:
        return True
    return False


def _is_recent_trading_day(candidate: datetime.date, ref: datetime.date) -> bool:
    """Check if candidate is a recent trading day relative to ref (within weekend)."""
    delta = (ref - candidate).days
    if delta <= 0:
        return False
    if ref.weekday() >= 5:  # ref is weekend
        last_trading = ref - timedelta(days=ref.weekday() - 4)
        return candidate == last_trading
    if delta == 1 and ref.weekday() == 0 and candidate.weekday() == 4:
        return True
    return False


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
    trading_status = _cn_trading_status(observed)
    stale_row_count = _stale_row_count(rows, observed, trading_status)
    freshness_summary = _freshness_summary(rows, observed, trading_status)
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
        "trade_date": observed.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
        "trading_status": trading_status,
        "total_rows": int(total_rows),
        "unique_code_count": int(codes.loc[row_valid_codes].nunique()),
        "duplicate_code_count": duplicate_code_count,
        "invalid_code_count": invalid_code_count,
        "market_counts": market_counts,
        "field_non_null_counts": field_non_null_counts,
        "field_coverage": field_coverage,
        "latest_quote_time": quote_times.max() if not quote_times.empty else None,
        "stale_row_count": stale_row_count,
        "freshness_summary": freshness_summary,
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


def _stale_row_count(frame: pd.DataFrame, observed_at: datetime, trading_status: str) -> int:
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
    if trading_status in ("post_close", "lunch_break", "non_trading_day"):
        # After close or lunch or non-trading: same-day quotes are not stale
        observed_local = observed_at.astimezone(timezone(timedelta(hours=8)))
        same_day = row_times.dt.tz_convert(timezone(timedelta(hours=8))).dt.date == observed_local.date()
        return int(((ages > _STALE_AFTER) & ~same_day.fillna(False)).sum())
    return int((ages > _STALE_AFTER).sum())


def _freshness_summary(
    frame: pd.DataFrame, observed_at: datetime, trading_status: str
) -> dict[str, int]:
    """Summarize quote freshness categories."""
    quote_times = _column_as_text(frame, "quote_time")
    counts: dict[str, int] = {}
    for qt in quote_times:
        freshness, quote_status = _quote_freshness(qt, observed_at, trading_status)
        key = f"{freshness}|{quote_status}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _supplier_quote_timestamp(value: str, observed_at: datetime) -> pd.Timestamp | None:
    """Parse a quote time string into a UTC pd.Timestamp (delegates to _parse_quote_datetime)."""
    parsed = _parse_quote_datetime(value, observed_at)
    if parsed is None:
        return None
    return pd.Timestamp(parsed).tz_convert(timezone.utc)


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

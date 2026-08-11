"""数据质量评估 —— 从 local_workflow.py 提取.

分钟质量分级、整体质量判定、过期日线汇总、数据源错误汇总.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

from marketbase.daily_collector import DailyCollectionReport


def _compute_minute_quality(minute_audit: dict[str, object] | None) -> str:
    """Compute minute data quality level: full | partial | failed | not_requested."""
    if not minute_audit or minute_audit.get("status") == "not_requested":
        return "not_requested"
    if minute_audit.get("append_error") or minute_audit.get("sequence_error"):
        return "failed"
    if minute_audit.get("status") not in ("collected",):
        return "failed"
    seq_audit = minute_audit.get("sequence_audit")
    if not isinstance(seq_audit, dict):
        return "partial"
    actual = int(seq_audit.get("actual_minutes", 0))
    missing = int(seq_audit.get("missing_minute_count", 0))
    breaks = int(seq_audit.get("continuity_break_count", 0))
    expected = int(seq_audit.get("total_expected_minutes", 240))
    if actual == 0:
        return "failed"
    if actual < expected * 0.20:
        return "static_only"
    if actual >= max(expected * 0.83, 200) and missing <= 40 and breaks <= 3:
        return "full"
    return "partial"


def _quality_status(
    market_audit: dict[str, object],
    daily_report: DailyCollectionReport,
    bse_codes_set: set[str],
    *,
    minute_audit: dict[str, object] | None = None,
    classification: pd.DataFrame | None = None,
    phase: str = "post_close",
) -> tuple[str, list[str]]:
    """Return ('data_ready'|'data_ready_static_only'|'partial'|'data_not_ready', reason_codes).

    'data_ready' means all required data dimensions are present, complete,
    and auditable for downstream strategy engine consumption.

    'data_ready_static_only' means basic data (market, daily, classification)
    are complete but minute data is insufficient for VWAP/momentum/sector
    reversal strategies; static trend screening remains usable.

    'partial' means some data dimensions are incomplete but usable — the
    collection has enough data for basic analysis but lacks coverage in
    non-critical dimensions.

    'data_not_ready' means critical data is missing and no strategy should
    consume this collection.
    """
    if phase == "lunch_break":
        return "data_not_ready", ["session_not_tradable"]

    _valid_phases = {"post_close", "intraday_1300", "intraday_1400", "intraday_1430"}
    if phase not in _valid_phases:
        return "data_not_ready", ["invalid_phase"]

    if classification is None or classification.empty:
        return "data_not_ready", ["classification_missing"]

    minute_quality = _compute_minute_quality(minute_audit)
    if minute_quality == "failed":
        return "data_not_ready", ["minute_failed"]

    # 静态数据不足标记：继续检查 market/daily/classification，不提前返回
    minute_static_only = (minute_quality == "static_only")

    audit_status = market_audit.get("status", "")
    if audit_status == "partial":
        return "data_not_ready", ["market_coverage_insufficient"]

    coverage_gaps = market_audit.get("coverage_gaps", [])
    if isinstance(coverage_gaps, list):
        for gap in coverage_gaps:
            gap_str = str(gap)
            if gap_str.startswith("provider_error_count"):
                continue
            # Coverage gaps are non-critical — data is still usable
            return "partial", ["field_coverage_below_threshold"]

    field_coverage = market_audit.get("field_coverage", {})
    if isinstance(field_coverage, dict) and field_coverage:
        market_coverage = sum(float(v) for v in field_coverage.values()) / max(len(field_coverage), 1)
    else:
        market_coverage = 1.0

    daily_success = daily_report.success_count + daily_report.cache_hit_count
    daily_rate = daily_success / max(daily_report.total_count, 1)

    bse_total = len(bse_codes_set)
    bse_unavailable = sum(
        1 for entry in daily_report.short_history
        if entry.get("code", "") in bse_codes_set
    )
    bse_rate = (bse_total - bse_unavailable) / max(bse_total, 1) if bse_total else 1.0

    if market_coverage < 0.5 or daily_rate < 0.5:
        return "data_not_ready", ["market_coverage_insufficient"]

    # ── 分类覆盖率检查 ──
    reason_codes: list[str] = []
    class_coverage = 1.0
    if classification is not None and not classification.empty:
        if "coverage_status" in classification.columns:
            covered = (classification["coverage_status"] == "mapped").sum()
            total = len(classification)
            class_coverage = covered / max(total, 1)
            if class_coverage < 0.95:
                reason_codes.append("classification_coverage_insufficient")
        # 产业链覆盖率独立检查：产业链映射不足时无法支持主题比较
        if "supply_chain" in classification.columns:
            sc_filled = (classification["supply_chain"].notna() & (classification["supply_chain"].astype(str).str.strip() != "")).sum()
            sc_coverage = sc_filled / max(len(classification), 1)
            if sc_coverage < 0.5:
                reason_codes.append("supply_chain_coverage_insufficient")
    if class_coverage < 0.8:
        return "partial", ["classification_coverage_insufficient"]

    if market_coverage < 0.95 or daily_rate < 0.95 or (bse_total > 0 and bse_rate < 0.8):
        reason_codes.append("daily_stale")
        if minute_static_only:
            # 分钟不足 + market/daily 也有缺口 → 综合降级
            return "partial", ["minute_insufficient"] + reason_codes
        return "partial", reason_codes

    if minute_static_only:
        return "data_ready_static_only", ["minute_insufficient"] + reason_codes
    if minute_quality == "partial":
        return "data_ready_static_only", ["minute_coverage_below_threshold"] + reason_codes
    if reason_codes:
        return "partial", reason_codes
    return "data_ready", []


def _effective_daily_date(observed_at: datetime) -> str:
    """Compute the effective reference date for daily data freshness.

    During intraday sessions (before 15:00) on a trading day, the latest
    trading day before today is used since today's OHLC hasn't settled yet.
    After 15:00 on a trading day, today's date is used.
    On non-trading days, the latest trading day is used.
    """
    from marketbase.trade_calendar import is_trading_day, latest_trading_day

    local = observed_at.astimezone(timezone(timedelta(hours=8)))
    local_date = local.date()

    # After 15:00 on a trading day: today's daily data is settled
    if is_trading_day(local) and local.hour >= 15:
        return local_date.isoformat()

    # Intraday or non-trading day: find the latest trading day before today
    return latest_trading_day(local_date - timedelta(days=1)).isoformat()


def _stale_daily_summary(
    daily_report: DailyCollectionReport,
    observed_at: datetime,
    *,
    frame: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Summarize stocks whose latest daily data is not from the current trade date.

    Separates codes into three categories:
      - stale_codes: codes with last_trade_date not matching current trade date
      - short_history_codes: codes with insufficient history rows (< 250 days)
      - failed_codes: codes with fetch errors

    Uses daily_report.latest_date_by_code for per-code stale detection,
    daily_report.latest_date_distribution for aggregate counts,
    daily_report.errors for fetch failures, daily_report.short_history for
    short-history detection, and snapshot quote_time for unknown quote time
    detection.
    """
    trade_date = _effective_daily_date(observed_at)
    unknown_quote_time_codes: list[str] = []

    # ── failed_codes: 采集失败的代码 ──
    failed_codes = sorted(str(c) for c in daily_report.errors.keys())

    # ── stale_codes: 成功采集但 date 非当日的代码（排除 failed） ──
    stale_codes: list[str] = []
    if daily_report.latest_date_by_code:
        for code, latest_date in daily_report.latest_date_by_code.items():
            if latest_date != trade_date and code not in daily_report.errors:
                stale_codes.append(code)
    stale_codes.sort()

    # ── short_history_codes: 历史行数不足的代码（< 250 天） ──
    short_history_codes: list[str] = []
    for entry in daily_report.short_history:
        code = str(entry.get("code", ""))
        actual_rows = int(entry.get("actual_rows", 0))
        if actual_rows < 250 and code:
            short_history_codes.append(code)
    short_history_codes.sort()

    # 从 latest_date_distribution 计算 stale 数量（非当日日期的代码）
    stale_from_dist = sum(
        count for date_str, count in daily_report.latest_date_distribution.items()
        if date_str != trade_date
    )
    total_stale = stale_from_dist

    # 从快照 quote_time 检测未知报价时间
    if frame is not None and "quote_time" in frame.columns and "code" in frame.columns:
        snapshot_unknown = frame.loc[
            frame["quote_time"].isna() | (frame["quote_time"].astype(str).str.strip() == ""),
            "code",
        ].tolist()
        for code in snapshot_unknown:
            code_str = str(code)
            if code_str not in unknown_quote_time_codes and code_str not in stale_codes:
                unknown_quote_time_codes.append(code_str)

    return {
        "expected_latest_date": trade_date,
        "total_stocks": daily_report.total_count,
        "stale_count": total_stale,
        "stale_codes": stale_codes,
        "stale_codes_count": len(stale_codes),
        "stale_by_date": {
            date_str: count
            for date_str, count in daily_report.latest_date_distribution.items()
            if date_str != trade_date
        },
        "failed_codes": failed_codes,
        "failed_count": len(failed_codes),
        "short_history_codes": short_history_codes,
        "short_history_count": len(short_history_codes),
        "unknown_quote_time_count": len(unknown_quote_time_codes),
        "unknown_quote_time_codes": unknown_quote_time_codes[:50],
        "unknown_quote_time_codes_truncated": len(unknown_quote_time_codes) > 50,
    }


def _provider_errors(
    result: Any, report: DailyCollectionReport
) -> list[str]:
    from marketbase.pipeline.helpers import _neutral_text
    values = list(getattr(result, "audit", {}).get("provider_errors", []))
    values.extend(report.errors.values())
    return [_neutral_text(str(value)) for value in values]


def _apply_degradation_flags(
    frame: pd.DataFrame,
    stale_summary: dict[str, object],
    daily_report: DailyCollectionReport,
) -> None:
    """Add is_untradable and is_incomparable flags to the frame.

    is_untradable: stale daily data, unknown quote_time, or collection error.
    is_incomparable: cannot be used for technical comparison (missing indicators).
    """
    if "code" not in frame.columns:
        return
    stale_codes = set(str(c) for c in stale_summary.get("stale_codes", []))
    unknown_codes = set(str(c) for c in stale_summary.get("unknown_quote_time_codes", []))
    error_codes = set(str(c) for c in daily_report.errors.keys())
    insufficient_codes = set(
        str(entry.get("code", "")) for entry in daily_report.indicator_insufficient
    )

    frame["is_untradable"] = frame["code"].astype(str).isin(
        stale_codes | unknown_codes | error_codes
    )
    frame["is_incomparable"] = frame["is_untradable"] | frame["code"].astype(str).isin(
        insufficient_codes
    )
    if "tradable" in frame.columns:
        frame["tradable"] = frame["tradable"].fillna(False).astype(bool) & ~frame["is_untradable"]

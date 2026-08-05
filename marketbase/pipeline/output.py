"""产物写入与 manifest 生成 —— 从 local_workflow.py 提取.

负责将所有 DataFrame 写入 CSV/JSON/Parquet，生成 manifest 和 latest handoff.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pandas as pd

from marketbase.market_collector import MarketCollectionResult, OUTPUT_FIELDS
from marketbase.daily_collector import DailyCollectionReport
from marketbase.daily_collector import _MIN_INDICATOR_ROWS  # pyright: ignore[reportPrivateUsage]
from marketbase.indicators import compute_rps20

from marketbase.pipeline.helpers import (
    _neutral_text,
    _json_value,
    _write_csv_atomic,
    _write_json_atomic,
    _write_text_atomic,
    _publish_latest,
    _file_records,
    _cache_paths,
    _error_summary,
    _INDICATOR_VALUE_FIELDS,
    _merge_indicators_to_snapshot,
)
from marketbase.pipeline.quality import (
    _compute_minute_quality,
    _quality_status,
    _stale_daily_summary,
    _provider_errors,
    _apply_degradation_flags,
    _effective_daily_date,
)
from marketbase.pipeline.index_module import _run_index_collection
from marketbase.pipeline.industry import _run_industry_aggregation


def _write_outputs_and_manifest(
    run_dir: Path,
    root: Path,
    observed_at: datetime,
    frame: pd.DataFrame,
    indicators_df: pd.DataFrame,
    classification: pd.DataFrame,
    market_audit: dict[str, object],
    classification_audit: dict[str, object],
    result: MarketCollectionResult,
    daily_report: DailyCollectionReport,
    codes: list[str],
    minute_audit: dict[str, object] | None = None,
    collection_started_at: str | None = None,
    emit: Callable[[str], None] | None = None,
    cache_root: Path | None = None,
    *,
    phase: str = "post_close",
    intraday_minutes_audit: dict[str, object] | None = None,
    intraday_minutes_path: str | None = None,
) -> dict[str, object]:
    """Write all output files, manifest, and latest handoff; return summary."""
    collection_completed_at = datetime.now().astimezone().isoformat()
    _emit = emit or (lambda msg: None)
    _cache = cache_root or (root / "cache")

    # Sort indicators by code before writing
    indicators_df_sorted = indicators_df.sort_values("code", ignore_index=True) if not indicators_df.empty else indicators_df
    # RPS20 全市场排名
    if not indicators_df_sorted.empty:
        rps20 = compute_rps20(indicators_df_sorted)
        indicators_df_sorted["rps20"] = cast(pd.Series, indicators_df_sorted["code"]).map(rps20)

    # 合并日线指标到快照
    frame = _merge_indicators_to_snapshot(frame, indicators_df_sorted)

    # 先计算 stale_daily 摘要并应用降级标记，再写出快照
    stale_summary = _stale_daily_summary(daily_report, observed_at, frame=frame)
    _apply_degradation_flags(frame, stale_summary, daily_report)

    # 调整 daily_success：排除 stale 代码（failed 已不在 success/cache_hit 中）
    adjusted_daily_success = (
        daily_report.success_count
        + daily_report.cache_hit_count
        - stale_summary.get("stale_codes_count", 0)
    )

    _write_csv_atomic(run_dir / "market_snapshot.csv", frame)
    _write_json_atomic(
        run_dir / "market_snapshot.json",
        {"schema_version": 1, "generated_at": observed_at.isoformat(), "rows": _json_value(frame.to_dict(orient="records"))},
    )
    _write_csv_atomic(run_dir / "daily_indicators.csv", indicators_df_sorted)
    _write_csv_atomic(run_dir / "classification_map.csv", classification)
    _write_csv_atomic(root / "classification_map.csv", classification)

    # 指数数据
    index_df, index_intraday_ready = _run_index_collection(frame, _cache, observed_at, _emit)
    if index_df is None or index_df.empty:
        index_df = pd.DataFrame(columns=["code", "name", "date", "close"])
        _emit("index collection: 0 indices (all failed)")
    _write_csv_atomic(run_dir / "index_data.csv", index_df)

    # 行业聚合
    industry_df = _run_industry_aggregation(frame, indicators_df_sorted, _emit)
    _write_csv_atomic(run_dir / "industry_agg.csv", industry_df)

    total_codes = len(codes)
    cache_coverage = total_codes - len(daily_report.invalid_or_missing_cache)
    daily_audit_payload = asdict(daily_report)
    daily_audit_payload["checkpoint_path"] = str(daily_report.checkpoint_path.resolve())
    daily_audit_payload["cache_root"] = str(daily_report.cache_root.resolve())
    daily_audit_payload["errors"] = {code: _neutral_text(error) for code, error in daily_report.errors.items()}
    daily_audit_payload["cache_coverage_count"] = cache_coverage
    daily_audit_payload["cache_coverage_rate"] = cache_coverage / total_codes if total_codes else 0.0
    daily_audit_payload.pop("indicators", None)
    daily_audit_payload.pop("latest_date_by_code", None)
    daily_audit = _json_value(daily_audit_payload)

    # --- indicator quality breakdown ---
    indicator_records = len(daily_report.indicators)
    indicator_any_valid = sum(
        1 for entry in daily_report.indicators
        if _indicator_has_value(entry, _INDICATOR_VALUE_FIELDS)
    )
    indicator_all_valid = sum(
        1 for entry in daily_report.indicators
        if all(
            (val := entry.get(field)) is not None and not (isinstance(val, float) and math.isnan(val))
            for field in _INDICATOR_VALUE_FIELDS
        )
    )
    indicator_all_empty = indicator_records - indicator_any_valid
    bse_codes_set = set(frame.loc[frame["market"] == "bj", "code"].tolist()) if "market" in frame.columns else set()
    bse_history_unavailable = sorted(
        c for c in bse_codes_set
        if c in set(daily_report.errors.keys())
        or any(
            entry.get("code") == c and int(cast(float, entry.get("actual_rows", 0))) < _MIN_INDICATOR_ROWS
            for entry in daily_report.short_history
        )
    )

    from marketbase.pipeline.helpers import _detect_session_slug
    session_phase = _detect_session_slug(observed_at, phase)

    quality_status, quality_reason_codes = _quality_status(market_audit, daily_report, bse_codes_set, minute_audit=minute_audit, classification=classification, phase=session_phase)

    # ── 分钟连续性 ──
    minute_continuity: dict[str, object] = {}
    if isinstance(minute_audit, dict):
        seq_audit = minute_audit.get("sequence_audit")
        if isinstance(seq_audit, dict):
            minute_continuity = {
                "actual_minutes": int(seq_audit.get("actual_minutes", 0)),
                "missing_minute_count": int(seq_audit.get("missing_minute_count", 0)),
                "continuity_break_count": int(seq_audit.get("continuity_break_count", 0)),
                "total_expected_minutes": int(seq_audit.get("total_expected_minutes", 240)),
            }
        minute_status = minute_audit.get("status", "not_requested")
        minute_continuity["status"] = str(minute_status) if minute_status else "not_requested"
        minute_reason = minute_audit.get("reason")
        if minute_reason:
            minute_continuity["reason"] = str(minute_reason)
    else:
        minute_continuity = {"status": "not_requested", "actual_minutes": 0, "missing_minute_count": 0, "continuity_break_count": 0, "total_expected_minutes": 0}

    # ── 分类覆盖率 ──
    classification_coverage: dict[str, object] = {}
    if isinstance(classification_audit, dict):
        classification_coverage = {
            "total": classification_audit.get("unique_code_count", 0),
            "covered": classification_audit.get("covered_count", 0),
            "missing": classification_audit.get("missing_count", 0),
            "industry_coverage_count": classification_audit.get("industry_coverage_count", 0),
            "concepts_coverage_count": classification_audit.get("concepts_coverage_count", 0),
            "supply_chain_coverage_count": classification_audit.get("supply_chain_coverage_count", 0),
            "industry_coverage": (
                classification_audit["industry_coverage_count"] / max(classification_audit.get("unique_code_count", 1), 1)
                if classification_audit.get("unique_code_count")
                else 0
            ),
            "supply_chain_coverage": (
                classification_audit["supply_chain_coverage_count"] / max(classification_audit.get("unique_code_count", 1), 1)
                if classification_audit.get("unique_code_count")
                else 0
            ),
            "source_counts": classification_audit.get("source_counts", {}),
        }
    elif classification is not None and not classification.empty:
        total = len(classification)
        if "industry" in classification.columns:
            filled = classification["industry"].notna() & (classification["industry"].astype(str).str.strip() != "")
            classification_coverage["industry_coverage"] = float(filled.sum()) / max(total, 1)
        if "supply_chain" in classification.columns:
            filled_sc = classification["supply_chain"].notna() & (classification["supply_chain"].astype(str).str.strip() != "")
            classification_coverage["supply_chain_coverage"] = float(filled_sc.sum()) / max(total, 1)
        classification_coverage["total"] = total

    trade_date = _effective_daily_date(observed_at)
    audit = {
        "schema_version": 1,
        "quality_status": quality_status,
        "quality_reason_codes": quality_reason_codes,
        "trade_date": trade_date,
        "session_phase": session_phase,
        "observed_at": observed_at.isoformat(),
        "coverage_gaps": _json_value(market_audit.get("coverage_gaps", [])),
        "duplicate_count": int(market_audit.get("duplicate_code_count", 0)),
        "provider_errors": _provider_errors(result, daily_report),
        "field_coverage": _json_value(market_audit.get("field_coverage", {})),
        "minute_continuity": minute_continuity,
        "daily_staleness": stale_summary,
        "classification_coverage": classification_coverage,
        "market": _json_value(market_audit),
        "daily": daily_audit,
        "indicators": {
            "records_generated": indicator_records,
            "any_valid": indicator_any_valid,
            "all_valid": indicator_all_valid,
            "all_empty": indicator_all_empty,
            "insufficient_history_count": len(daily_report.indicator_insufficient),
            "errors": [],
        },
        "bse": {
            "total": len(bse_codes_set),
            "history_unavailable": bse_history_unavailable,
            "history_unavailable_count": len(bse_history_unavailable),
        },
        "classification": _json_value(classification_audit),
        "intraday_minutes": intraday_minutes_audit or {"status": "not_requested"},
        "stale_daily": stale_summary,
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
    }
    _write_json_atomic(run_dir / "data_audit.json", audit)

    # ── 原始响应归档 ──
    raw_response = frame.attrs.get("raw_response")
    if raw_response is not None:
        _write_json_atomic(
            run_dir / "raw_snapshot_response.json",
            {
                "schema_version": 1,
                "generated_at": observed_at.isoformat(),
                "row_count": len(raw_response) if isinstance(raw_response, list) else 0,
                "data": _json_value(raw_response),
            },
        )

    # ── FIELDS.md 字段文档 ──
    _write_fields_md(run_dir / "FIELDS.md", observed_at)

    rows: dict[str, int] = {
        "market_snapshot_csv": len(frame),
        "market_snapshot_json": len(frame),
        "daily_indicators": len(indicators_df),
        "classification_map": len(classification),
        "index_data": len(index_df),
        "industry_agg": len(industry_df),
        "market_breadth": 1,
        "data_audit": 1,
        "workflow_log": 0,
    }
    if (run_dir / "raw_snapshot_response.json").exists():
        rows["raw_snapshot_response"] = 1
    if (run_dir / "FIELDS.md").exists():
        rows["fields_md"] = 1
    if intraday_minutes_path:
        rows["intraday_minutes_parquet"] = 1
    files = _file_records(run_dir, rows)
    manifest = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
        "run_dir": str(run_dir),
        "files": files,
        "cache_paths": _cache_paths(root),
        "errors": _error_summary(audit),
    }
    _write_json_atomic(run_dir / "manifest.json", manifest)
    latest_path = root / "latest_codex_input.json"
    _ = _publish_latest(
        latest_path,
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "collection_started_at": collection_started_at,
            "collection_completed_at": collection_completed_at,
            "run_dir": str(run_dir),
            "market_snapshot_path": str((run_dir / "market_snapshot.json").resolve()),
            "daily_indicators_path": str((run_dir / "daily_indicators.csv").resolve()),
            "classification_map_path": str((run_dir / "classification_map.csv").resolve()),
            "index_data_path": str((run_dir / "index_data.csv").resolve()),
            "industry_agg_path": str((run_dir / "industry_agg.csv").resolve()),
            "market_breadth_path": str((run_dir / "market_breadth.json").resolve()),
            "data_audit_path": str((run_dir / "data_audit.json").resolve()),
            "manifest_path": str((run_dir / "manifest.json").resolve()),
            "intraday_minutes_path": (
                str((run_dir / "intraday_minutes.parquet").resolve())
                if intraday_minutes_path else None
            ),
            "cache_paths": _cache_paths(root),
            "quality_status": audit["quality_status"],
            "market_rows": len(frame),
            "indicator_rows": indicator_records,
            "indicator_any_valid": indicator_any_valid,
            "indicator_all_empty": indicator_all_empty,
            "bse_total": len(bse_codes_set),
            "bse_history_unavailable": len(bse_history_unavailable),
            "daily_success": adjusted_daily_success,
            "daily_failure": daily_report.failure_count,
            "daily_stale": stale_summary.get("stale_codes_count", 0),
            "daily_short_history": stale_summary.get("short_history_count", 0),
            "cache_hits": daily_report.cache_hit_count,
        },
    )

    # ── latest handoff: 按质量状态分别写入，永远不指向失败或半成品目录 ──
    if quality_status == "data_ready":
        _ = _publish_latest(
            root / "latest_full_ready.json",
            {
                "schema_version": 1,
                "run_dir": str(run_dir),
                "quality_status": quality_status,
                "generated_at": observed_at.isoformat(),
                "trade_date": trade_date,
            },
        )
        _ = _publish_latest(
            root / "latest_static_ready.json",
            {
                "schema_version": 1,
                "run_dir": str(run_dir),
                "quality_status": quality_status,
                "generated_at": observed_at.isoformat(),
                "trade_date": trade_date,
            },
        )
        # 兼容旧路径
        _ = _publish_latest(
            root / "latest_complete.json",
            {
                "schema_version": 1,
                "run_dir": str(run_dir),
                "quality_status": quality_status,
                "generated_at": observed_at.isoformat(),
                "trade_date": trade_date,
            },
        )
    elif quality_status == "data_ready_static_only":
        _ = _publish_latest(
            root / "latest_static_ready.json",
            {
                "schema_version": 1,
                "run_dir": str(run_dir),
                "quality_status": quality_status,
                "generated_at": observed_at.isoformat(),
                "trade_date": trade_date,
            },
        )
        # 兼容旧路径
        _ = _publish_latest(
            root / "latest_complete.json",
            {
                "schema_version": 1,
                "run_dir": str(run_dir),
                "quality_status": quality_status,
                "generated_at": observed_at.isoformat(),
                "trade_date": trade_date,
            },
        )

    return {
        "run_dir": str(run_dir),
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
        "market_rows": len(frame),
        "daily_success": adjusted_daily_success,
        "daily_failure": daily_report.failure_count,
        "indicator_rows": len(indicators_df),
        "classification_rows": len(classification),
        "latest_input_path": str(latest_path),
        "files": files,
    }


def _indicator_has_value(entry: dict[str, object], fields: tuple[str, ...]) -> bool:
    """Check if any indicator field has a non-None, non-NaN value."""
    for field in fields:
        val = entry.get(field)
        if val is not None and not (isinstance(val, float) and math.isnan(val)):
            return True
    return False


# ── FIELDS.md 字段文档 ─────────────────────────────────────────────────

_FIELD_DESCRIPTIONS: dict[str, str] = {
    "code": "股票代码，6位数字字符串",
    "name": "股票名称",
    "market": "市场标识: sh(上海), sz(深圳), bj(北交所)",
    "price": "最新价(元)",
    "pre_close": "前收盘价(元)",
    "open": "开盘价(元)",
    "high": "最高价(元)",
    "low": "最低价(元)",
    "change_pct": "涨跌幅(%)",
    "volume": "成交量(股)",
    "amount": "成交额(元)",
    "turnover_rate": "换手率(%)",
    "volume_ratio": "量比",
    "total_mv": "总市值(元)",
    "circ_mv": "流通市值(元)",
    "pe_ratio": "市盈率",
    "pb_ratio": "市净率",
    "quote_time": "行情时间",
    "observed_at": "观测时间(ISO 8601)",
    "source": "数据来源",
    "industry": "所属行业",
    "concepts": "概念题材",
    "board": "板块: 主板/创业板/科创板/北交所/中小板",
    "is_st": "是否ST或*ST股票",
    "is_suspended": "是否停牌",
    "delist_risk": "退市风险: 名称含*ST/退市/PT",
    "listed_days": "上市天数(自上市至今)",
    "trade_date": "交易日期",
}


def _write_fields_md(path: Path, observed_at: datetime) -> None:
    """Generate FIELDS.md documenting all fields in market_snapshot.csv."""
    lines = [
        "# Market Snapshot 字段文档",
        "",
        f"生成时间: {observed_at.isoformat()}",
        "",
        "| 字段名 | 描述 |",
        "|--------|------|",
    ]
    for field in OUTPUT_FIELDS:
        desc = _FIELD_DESCRIPTIONS.get(field, "")
        lines.append(f"| {field} | {desc} |")
    lines.append("")
    _write_text_atomic(path, "\n".join(lines) + "\n")

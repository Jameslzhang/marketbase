"""MarketBase pipeline subpackage — 采集流水线的解耦模块.

从 local_workflow.py 提取的独立模块:
  - helpers:   通用工具函数（原子 I/O、JSON 序列化、文件锁、目录创建）
  - progress:  进度条渲染
  - steps:     流水线各步骤（快照、日线、量比、审计、分类）
  - quality:   数据质量评估（分钟质量、整体质量、过期日线）
  - output:    产物写入与 manifest 生成
  - index_module: 指数数据采集 + 行业聚合
"""

from marketbase.pipeline.helpers import (
    _observed_at,
    _neutral_text,
    _json_value,
    _frame_records,
    _unique_codes,
    _neutralize_frame,
    _neutralize_output,
    _atomic_replace,
    _write_json_atomic,
    _write_csv_atomic,
    _write_text_atomic,
    _lock_file,
    _unlock_file,
    _try_lock_nonblocking,
    _file_records,
    _line_count,
    _error_summary,
    _cache_paths,
    _create_run_directory,
    _detect_session_slug,
    _publish_latest,
    _parse_generated_at,
    _existing_generated_at,
    _INDICATOR_VALUE_FIELDS,
    _INDICATOR_FIELDS,
    _merge_indicators_to_snapshot,
)
from marketbase.pipeline.progress import (
    _ts,
    _render_bar,
    _clear_progress_line,
    _write_progress_line,
)
from marketbase.pipeline.steps import (
    _run_market_collection,
    _run_daily_collection,
    _run_volume_ratio,
    _run_tradability,
    _run_audit_and_classification,
    _run_enrich_classification,
    _run_minute_snapshot,
    _indicator_has_value,
    _call_market_collector,
    _ensure_market_cache,
    _existing_map,
    _supply_chain_path,
)
from marketbase.pipeline.quality import (
    _compute_minute_quality,
    _quality_status,
    _stale_daily_summary,
    _provider_errors,
)
from marketbase.pipeline.output import (
    _write_outputs_and_manifest,
)
from marketbase.pipeline.index_module import (
    _run_index_collection,
    _INDEX_CODES,
    _INDEX_DATA_COLUMNS,
)
from marketbase.pipeline.industry import (
    _run_industry_aggregation,
    _INDUSTRY_AGG_COLUMNS,
)

__all__ = [
    # helpers
    "_observed_at", "_neutral_text", "_json_value", "_frame_records",
    "_unique_codes", "_neutralize_frame", "_neutralize_output",
    "_atomic_replace", "_write_json_atomic", "_write_csv_atomic",
    "_write_text_atomic", "_lock_file", "_unlock_file",
    "_try_lock_nonblocking", "_file_records", "_line_count",
    "_error_summary", "_cache_paths", "_create_run_directory",
    "_detect_session_slug", "_publish_latest", "_parse_generated_at",
    "_existing_generated_at", "_INDICATOR_VALUE_FIELDS", "_INDICATOR_FIELDS",
    "_merge_indicators_to_snapshot",
    # progress
    "_ts", "_render_bar", "_clear_progress_line", "_write_progress_line",
    # steps
    "_run_market_collection", "_run_daily_collection", "_run_volume_ratio",
    "_run_tradability", "_run_audit_and_classification",
    "_run_enrich_classification", "_run_minute_snapshot",
    "_indicator_has_value", "_call_market_collector", "_ensure_market_cache",
    "_existing_map", "_supply_chain_path",
    # quality
    "_compute_minute_quality", "_quality_status", "_stale_daily_summary",
    "_provider_errors", "_effective_daily_date",
    # output
    "_write_outputs_and_manifest",
    # index_module
    "_run_index_collection", "_INDEX_CODES", "_INDEX_DATA_COLUMNS",
    # industry
    "_run_industry_aggregation", "_INDUSTRY_AGG_COLUMNS",
]
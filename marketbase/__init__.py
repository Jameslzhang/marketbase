"""Objective A-share market data collection APIs."""

from marketbase.classify import build_classification_map
from marketbase.daily import daily_source_health_snapshot, fetch_daily_history
from marketbase.daily_collector import (
    DailyCollectionReport,
    DailyProgressEvent,
    collect_daily_universe,
    read_daily_cache,
)
from marketbase.data_audit import audit_market_snapshot
from marketbase.data_request import DataRequest, load_data_request, write_data_response
from marketbase.indicators import compute_daily_indicators, compute_vwap
from marketbase.live_workflow import (
    acquire_live_snapshot,
    fetch_reference_snapshot_with_bse_fallback,
    fetch_tencent_minute_rows,
)
from marketbase.market_collector import MarketCollectionResult, collect_market_snapshot
from marketbase.minute_collector import collect_requested_data
from marketbase.snapshot import (
    fetch_cn_snapshot,
    fetch_snapshot_with_fallback,
    snapshot_source_health_snapshot,
)

__version__ = "0.2.0"

__all__ = [
    "DataRequest",
    "DailyCollectionReport",
    "DailyProgressEvent",
    "MarketCollectionResult",
    "__version__",
    "acquire_live_snapshot",
    "audit_market_snapshot",
    "build_classification_map",
    "collect_daily_universe",
    "collect_market_snapshot",
    "collect_requested_data",
    "compute_daily_indicators",
    "compute_vwap",
    "daily_source_health_snapshot",
    "fetch_cn_snapshot",
    "fetch_daily_history",
    "fetch_reference_snapshot_with_bse_fallback",
    "fetch_snapshot_with_fallback",
    "fetch_tencent_minute_rows",
    "load_data_request",
    "read_daily_cache",
    "snapshot_source_health_snapshot",
    "write_data_response",
]

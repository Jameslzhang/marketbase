from __future__ import annotations

import ast
from pathlib import Path

import alphasift


OBJECTIVE_EXPORTS = {
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
}

REMOVED_MODULES = {
    "afternoon",
    "audit",
    "candidate_context",
    "chinese_output",
    "config",
    "context",
    "doctor",
    "dsa",
    "dsa_adapter",
    "dsa_provider",
    "evaluate",
    "filter",
    "full_market",
    "hotspot",
    "industry",
    "local_enrichment",
    "local_snapshot",
    "normalize",
    "overview",
    "performance_history",
    "pipeline",
    "post_analysis",
    "prefilter",
    "ranker",
    "report",
    "result_schema",
    "risk",
    "run_history",
    "scorer",
    "server",
    "snapshot_us",
    "source_history",
    "store",
    "strategy",
    "strategy_cards",
    "strategy_templates",
}


def test_package_exports_only_objective_api():
    assert set(alphasift.__all__) == OBJECTIVE_EXPORTS
    assert all(hasattr(alphasift, name) for name in OBJECTIVE_EXPORTS)


def test_removed_local_modules_are_absent():
    package_root = Path(alphasift.__file__).resolve().parent
    assert not {
        path.stem for path in package_root.glob("*.py")
    }.intersection(REMOVED_MODULES)
    assert not (package_root / "strategies").exists()


def test_objective_import_closure_does_not_reach_removed_modules():
    repository_root = Path(__file__).resolve().parents[1]
    roots = [repository_root / "local_workflow.py"] + [
        repository_root / "alphasift" / f"{name}.py"
        for name in (
            "classification_map",
            "daily",
            "daily_collector",
            "data_audit",
            "data_request",
            "live_workflow",
            "market_collector",
            "minute_collector",
            "neutral_indicators",
            "snapshot",
            "source_guard",
        )
    ]
    imported: set[str] = set()
    for path in roots:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.removeprefix("alphasift."))
            elif isinstance(node, ast.Import):
                imported.update(
                    alias.name.removeprefix("alphasift.") for alias in node.names
                )

    assert imported.isdisjoint(REMOVED_MODULES)

"""
V1→V2 迁移脚本 — 将旧记录迁移到新版 Schema
==============================================
按 §8.1 步骤 1 要求：迁移脚本必须在杰瑞股份和中科曙光的历史记录上回放。

用法:
    python scripts/migrate_v1_to_v2.py --input strategies/t1_processed_data.json
    python scripts/migrate_v1_to_v2.py --input data/daily_runs/2026-07-31/170721_postclose_objective_data
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ_SHANGHAI = timezone(timedelta(hours=8))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def migrate_v1_record(record: dict) -> dict:
    """将单条 V1 记录迁移到 V2 Schema"""
    computed = record.get("computed", {})
    snapshot = record.get("snapshot", {})
    daily = record.get("daily", {})

    return {
        "code": record.get("code", ""),
        "name": record.get("name", ""),
        "snapshot": snapshot,
        "daily": daily,
        "computed_v2": {
            "formula_version": "2.0.0-legacy-migration",
            "decision_id": f"migrated_{record.get('code', 'unknown')}",
            "strategy_profile_id": "watchlist_t1_v1",
            "strategy_version": "legacy",
            "channels": {
                "trend_continuation": {
                    "passed": computed.get("trend_aligned", False),
                    "production_buyable": True,
                    "reason": "migrated_from_v1",
                    "evidence": {},
                },
                "strong_pullback_reclaim": {
                    "passed": False,
                    "production_buyable": True,
                    "reason": "not_evaluated_in_v1",
                    "evidence": {},
                },
                "high_momentum": {
                    "passed": False,
                    "production_buyable": False,
                    "reason": "research_only",
                    "evidence": {},
                },
                "sector_reversal_challenger": {
                    "passed": False,
                    "production_buyable": False,
                    "reason": "shadow_only",
                    "evidence": {},
                },
            },
            "primary_channel": "trend_continuation" if computed.get("trend_aligned") else None,
            "dual_axis": {
                "decision": computed.get("dual_axis_decision", "reject"),
                "opportunity_quality": computed.get("opportunity_quality", "poor"),
                "tail_risk": computed.get("tail_risk", "low"),
                "op_score": computed.get("op_score", 0),
                "op_detail": computed.get("op_detail", ""),
                "tr_count": computed.get("tr_count", 0),
                "tr_flags": [],
                "reason_code": "migrated_from_v1",
            },
            "buy_zone": {
                "lower": computed.get("buy_zone", {}).get("first_lower", 0),
                "upper": computed.get("buy_zone", {}).get("first_upper", 0),
                "no_chase_price": computed.get("buy_zone", {}).get("first_upper", 0),
                "trigger_reference": 0,
                "empty": False,
                "confirmation_above_no_chase": False,
            },
            "sell_zone": {
                "first_lower": computed.get("sell_zone", {}).get("first_lower"),
                "first_upper": computed.get("sell_zone", {}).get("first_upper"),
                "first_center": computed.get("sell_zone", {}).get("first_center"),
                "second_lower": None,
                "second_upper": None,
                "second_center": None,
                "second_available": False,
                "error": None,
            },
            "protection": {
                "price": computed.get("protection_price", 0),
                "constructible": True,
                "structural_support": computed.get("protection_price", 0),
                "reason": "",
            },
            "fees": {
                "net_profit": 0,
                "net_loss": 0,
                "total_cost": 0,
                "fee_policy_id": "default_v1",
            },
            "upper_shadow": {"upper_shadow_ratio": 0.0, "long_upper_shadow": False},
            "industry_sync": {
                "pass": computed.get("industry_sync_ok", False),
                "detail": computed.get("industry_sync_detail", ""),
                "advance_ratio": 0.0,
            },
            "market": {"veto": False, "advance_ratio": 0.0, "broad_market_detail": 0},
            "entry_state": "deep_watch" if computed.get("dual_axis_decision") == "can_enter_candidate" else "rejected",
            "entry_state_history": [],
            "daily_context": {
                "trend_aligned": computed.get("trend_aligned", False),
                "boll_position": 0.5,
                "rsi14": daily.get("rsi14", 0),
                "atr14": computed.get("atr14", 0),
                "atr14_pct": computed.get("atr14_pct", 0),
                "rps20": daily.get("rps20", 0),
                "return_5d": daily.get("return_5d", 0),
                "return_20d": daily.get("return_20d", 0),
            },
        },
        "_migration": {
            "migrated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "original_formula_version": "1.0.0",
            "missing_fields": ["channels", "fees", "upper_shadow", "entry_state"],
            "status": "legacy_migrated",
        },
    }


def migrate_v1_file(input_path: Path, output_path: Optional[Path] = None) -> int:
    """迁移 V1 processed_data.json 到 V2"""
    if not input_path.exists():
        print(f"[ERROR] 输入文件不存在: {input_path}")
        return 1

    if output_path is None:
        output_path = input_path.parent / "t1_processed_data_v2_migrated.json"

    print(f"迁移: {input_path} → {output_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    stocks = data.get("stocks", [])
    migrated_stocks = []
    for s in stocks:
        migrated = migrate_v1_record(s)
        migrated_stocks.append(migrated)

    output = {
        "meta": {
            "source_run": data.get("meta", {}).get("source_run", "unknown"),
            "source_generated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "formula_version": "2.0.0-legacy-migration",
            "quality_status": data.get("meta", {}).get("quality_status", "unknown"),
            "strategy_profile_id": data.get("meta", {}).get("strategy_profile_id", "watchlist_t1_v1"),
            "strategy_version": "legacy",
            "lifecycle_contract_version": "1.6.0",
            "schema_version": "2.0.0",
            "execution_rule_version": "legacy",
            "skill_version": "legacy",
        },
        "market": data.get("market", {}),
        "summary": {
            "total": len(migrated_stocks),
            "candidates": sum(1 for s in migrated_stocks
                            if s["computed_v2"]["dual_axis"]["decision"] == "can_enter_candidate"),
            "conditional": sum(1 for s in migrated_stocks
                             if s["computed_v2"]["dual_axis"]["decision"] == "conditional_watch"),
            "rejected": sum(1 for s in migrated_stocks
                          if s["computed_v2"]["dual_axis"]["decision"] == "reject"),
        },
        "stocks": migrated_stocks,
        "audit": {
            "total_decisions": len(migrated_stocks),
            "metrics": {
                "total_decisions": len(migrated_stocks),
                "total_audits": 0,
                "by_category": {},
            },
        },
        "_migration_meta": {
            "migrated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "source_file": str(input_path),
            "migrated_count": len(migrated_stocks),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[OK] 迁移完成: {len(migrated_stocks)} 条记录 → {output_path}")
    return 0


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="V1→V2 迁移脚本")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    return migrate_v1_file(args.input, args.output)


if __name__ == "__main__":
    sys.exit(main())
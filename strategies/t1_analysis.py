"""
T1 Strategy — 端到端分析模块（V2 升级）
==========================================
作为 MarketBase 子模块，读取 t1_processed_data_v2.json 并生成分析报告。

使用 strategy_lifecycle 模块的确定性输出，只做用户可读文案生成。
不自行计算任何指标、不修改确定性结论、不跳过状态机。

用法:
    python local_workflow.py t1-analyze --v2
    python -m marketbase.t1_analysis --v2
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ_SHANGHAI = timezone(timedelta(hours=8))

DEFAULT_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "daily_runs"
DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "strategies" / "t1_processed_data.json"
DEFAULT_INPUT_V2 = Path(__file__).resolve().parent.parent / "strategies" / "t1_processed_data_v2.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "strategies" / "t1_analysis_report.md"
DEFAULT_OUTPUT_V2 = Path(__file__).resolve().parent.parent / "strategies" / "t1_analysis_report_v2.md"


# ============================================================================
# V1 分析（向后兼容）
# ============================================================================

def rank_candidates(stocks: list) -> dict:
    """Skill 1: a-share-t1-rank-sandbox — 排序筛选"""
    candidates = []
    conditional = []
    rejected = []

    for s in stocks:
        c = s.get("computed", {})
        decision = c.get("dual_axis_decision", "reject")
        if decision == "can_enter_candidate":
            candidates.append(s)
        elif decision == "conditional_watch":
            conditional.append(s)
        else:
            rejected.append(s)

    candidates.sort(key=lambda x: x["computed"].get("op_score", 0), reverse=True)
    conditional.sort(key=lambda x: x["computed"].get("op_score", 0), reverse=True)

    return {
        "candidates": candidates[:1],
        "conditional": conditional[:2],
        "rejected": rejected,
        "total": len(stocks),
    }


def quant_review(stocks: list) -> list:
    """Skill 2: a-share-quant-sandbox — 量化复核"""
    reviews = []
    for s in stocks:
        c = s["computed"]
        snap = s["snapshot"]
        checks = {
            "vwap_position": {
                "pass": c.get("vwap_position", 0) > 1.0,
                "value": c.get("vwap_position", 0),
                "threshold": "> 1.0",
            },
            "volume_ratio": {
                "pass": snap.get("volume_ratio", 0) > 1.2,
                "value": snap.get("volume_ratio", 0),
                "threshold": "> 1.2",
            },
            "industry_sync": {
                "pass": c.get("industry_sync_ok", False),
                "value": c.get("industry_sync_detail", ""),
                "threshold": "行业同步",
            },
            "trend_aligned": {
                "pass": c.get("trend_aligned", False),
                "value": "MA多头" if c.get("trend_aligned") else "MA未多头",
                "threshold": "MA5>MA10>MA20>MA60",
            },
            "rsi_range": {
                "pass": 40 <= float(s.get("daily", {}).get("rsi14", 0) or 0) <= 70,
                "value": round(float(s.get("daily", {}).get("rsi14", 0) or 0), 1),
                "threshold": "40-70",
            },
        }
        all_pass = all(v["pass"] for v in checks.values())
        reviews.append({
            "code": s["code"],
            "name": s["name"],
            "checks": checks,
            "all_pass": all_pass,
            "verdict": "PASS" if all_pass else "FAIL",
        })
    return reviews


def technical_review(stocks: list) -> list:
    """Skill 3: a-share-technical-lab — 技术面研判"""
    reviews = []
    for s in stocks:
        c = s["computed"]
        snap = s["snapshot"]
        buy_zone = c.get("buy_zone", {})
        sell_zone = c.get("sell_zone", {})
        protection = c.get("protection_price", 0)
        atr = c.get("atr14", 0)
        price = snap["price"]
        reward = sell_zone.get("first_center", price) - price
        risk = price - protection
        rr_ratio = round(reward / risk, 2) if risk > 0 else 0
        reviews.append({
            "code": s["code"],
            "name": s["name"],
            "price": price,
            "buy_zone": buy_zone,
            "sell_zone": sell_zone,
            "protection": protection,
            "atr": atr,
            "reward_risk_ratio": rr_ratio,
            "rr_verdict": "OK" if rr_ratio >= 1.20 else "LOW",
        })
    return reviews


def generate_report(data: dict, ranked: dict, quant_reviews: list, tech_reviews: list) -> str:
    """生成 Markdown 分析报告（V1）"""
    meta = data.get("meta", {})
    market = data.get("market", {})
    fm = market.get("full_market", {})
    lines = []
    lines.append("# T1 Strategy 分析报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now(TZ_SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**数据来源**: {meta.get('source_run', 'N/A')}")
    lines.append(f"**公式版本**: {meta.get('formula_version', 'N/A')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 市场概况")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 上涨占比 | {market.get('advance_ratio', 0):.1%} |")
    lines.append(f"| 平均涨幅 | {fm.get('avg_change_pct', 0):.2f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 筛选结果")
    lines.append("")
    lines.append(f"- **正式候选**: {len(ranked['candidates'])} 只")
    lines.append(f"- **条件观察**: {len(ranked['conditional'])} 只")
    lines.append(f"- **已拒绝**: {len(ranked['rejected'])} 只")
    lines.append("")
    if ranked["candidates"]:
        lines.append("### 正式候选")
        for s in ranked["candidates"]:
            c = s["computed"]
            snap = s["snapshot"]
            lines.append(f"**{s['code']} {s['name']}** | 价格: {snap['price']:.2f}")
            lines.append(f"- 买区: {c['buy_zone']['first_lower']:.2f} - {c['buy_zone']['first_upper']:.2f}")
            lines.append(f"- 保护位: {c['protection_price']:.2f}")
            lines.append("")
    if ranked["conditional"]:
        lines.append("### 条件观察")
        for s in ranked["conditional"]:
            c = s["computed"]
            lines.append(f"- **{s['code']} {s['name']}** | {c['op_detail']}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 量化复核")
    for r in quant_reviews:
        lines.append(f"### {r['code']} {r['name']}: {r['verdict']}")
        for check_name, check in r["checks"].items():
            icon = "✓" if check["pass"] else "✗"
            lines.append(f"- {icon} **{check_name}**: {check['value']} (阈值: {check['threshold']})")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 技术面研判")
    for t in tech_reviews:
        lines.append(f"### {t['code']} {t['name']}")
        lines.append(f"- 当前价: {t['price']:.2f}")
        lines.append(f"- 收益风险比: {t['reward_risk_ratio']} ({t['rr_verdict']})")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*报告由 T1 Strategy 分析引擎自动生成*")
    return "\n".join(lines)


# ============================================================================
# V2 分析（使用 strategy_lifecycle 模块）
# ============================================================================

def rank_candidates_v2(stocks: list) -> dict:
    """V2 候选筛选 — 读取 computed_v2 中的确定性双轴结论"""
    candidates = []
    conditional = []
    rejected = []

    for s in stocks:
        cv2 = s.get("computed_v2", {})
        da = cv2.get("dual_axis", {})
        decision = da.get("decision", "reject")
        if decision == "can_enter_candidate":
            candidates.append(s)
        elif decision == "conditional_watch":
            conditional.append(s)
        else:
            rejected.append(s)

    candidates.sort(key=lambda x: x["computed_v2"]["dual_axis"].get("op_score", 0), reverse=True)
    conditional.sort(key=lambda x: x["computed_v2"]["dual_axis"].get("op_score", 0), reverse=True)

    return {
        "candidates": candidates,
        "conditional": conditional,
        "rejected": rejected,
        "total": len(stocks),
    }


def quant_review_v2(stocks: list) -> list:
    """V2 量化复核 — 读取 Python 层已计算的确定性结果"""
    reviews = []
    for s in stocks:
        cv2 = s.get("computed_v2", {})
        bz = cv2.get("buy_zone", {})
        sz = cv2.get("sell_zone", {})
        pr = cv2.get("protection", {})
        fe = cv2.get("fees", {})
        dc = cv2.get("daily_context", {})
        ind = cv2.get("industry_sync", {})

        checks = {
            "buy_zone": {
                "pass": not bz.get("empty", True),
                "value": f"{bz.get('lower', 0):.2f} - {bz.get('upper', 0):.2f}",
                "threshold": "买区非空",
            },
            "no_chase": {
                "pass": not bz.get("confirmation_above_no_chase", True),
                "value": "未追价" if not bz.get("confirmation_above_no_chase", True) else "超禁追价",
                "threshold": "确认价 <= 禁追价",
            },
            "protection": {
                "pass": pr.get("constructible", False),
                "value": f"{pr.get('price', 0):.2f}" if pr.get("constructible") else "不可构造",
                "threshold": "保护位可构造",
            },
            "industry_sync": {
                "pass": ind.get("pass", False),
                "value": ind.get("detail", ""),
                "threshold": "行业同步",
            },
            "trend": {
                "pass": dc.get("trend_aligned", False),
                "value": "多头" if dc.get("trend_aligned") else "未多头",
                "threshold": "MA5>MA10>MA20>MA60",
            },
            "rsi": {
                "pass": 40 <= dc.get("rsi14", 0) <= 70,
                "value": round(dc.get("rsi14", 0), 1),
                "threshold": "40-70",
            },
        }
        all_pass = all(v["pass"] for v in checks.values())
        reviews.append({
            "code": s["code"],
            "name": s["name"],
            "checks": checks,
            "all_pass": all_pass,
            "verdict": "PASS" if all_pass else "FAIL",
        })
    return reviews


def generate_report_v2(data: dict, ranked: dict, quant_reviews: list) -> str:
    """生成 Markdown 分析报告（V2）"""
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    lines = []
    lines.append("# T1 全生命周期策略分析报告 (V2)")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now(TZ_SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**策略配置**: {meta.get('strategy_profile_id', 'N/A')}")
    lines.append(f"**策略版本**: {meta.get('strategy_version', 'N/A')}")
    lines.append(f"**合约版本**: {meta.get('lifecycle_contract_version', 'N/A')}")
    lines.append(f"**公式版本**: {meta.get('formula_version', 'N/A')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 市场概况")
    lines.append("")
    adv = data.get("market", {}).get("advance_ratio", 0)
    vetos = sum(1 for s in data.get("stocks", [])
                if s.get("computed_v2", {}).get("market", {}).get("veto", False))
    lines.append(f"- 上涨占比: {adv:.1%}")
    lines.append(f"- 市场否决: {vetos} 只")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 筛选结果")
    lines.append("")
    lines.append(f"- **正式候选**: {len(ranked['candidates'])} 只")
    lines.append(f"- **条件观察**: {len(ranked['conditional'])} 只")
    lines.append(f"- **已拒绝**: {len(ranked['rejected'])} 只")
    lines.append("")

    if ranked["candidates"]:
        lines.append("### 正式候选")
        lines.append("")
        for s in ranked["candidates"]:
            cv2 = s["computed_v2"]
            bz = cv2["buy_zone"]
            sz = cv2["sell_zone"]
            pr = cv2["protection"]
            da = cv2["dual_axis"]
            fe = cv2["fees"]
            snap = s["snapshot"]
            lines.append(f"#### {s['code']} {s['name']}")
            lines.append(f"- **通道**: {cv2.get('primary_channel', 'N/A')}")
            lines.append(f"- **双轴**: 机会{da['opportunity_quality']} × 风险{da['tail_risk']} = {da['decision']}")
            lines.append(f"- **机会评分**: {da['op_score']} ({da['op_detail']})")
            lines.append(f"- **尾部风险**: {da['tr_count']} 个标签 ({', '.join(da.get('tr_flags', []))})")
            lines.append(f"- **入场状态**: {cv2.get('entry_state', 'N/A')}")
            lines.append(f"- **买区**: {bz['lower']:.2f} - {bz['upper']:.2f} (禁追: {bz['no_chase_price']:.2f})")
            lines.append(f"- **保护位**: {pr['price']:.2f} (可构造: {pr['constructible']})")
            lines.append(f"- **卖区1**: {sz.get('first_lower', 0):.2f} - {sz.get('first_upper', 0):.2f}")
            if sz.get('second_available'):
                lines.append(f"- **卖区2**: {sz.get('second_lower', 0):.2f} - {sz.get('second_upper', 0):.2f}")
            lines.append(f"- **手续费后净收益**: {fe.get('net_profit', 0):.2f}")
            lines.append(f"- **行业同步**: {cv2.get('industry_sync', {}).get('detail', 'N/A')}")
            lines.append(f"- **当前价**: {snap['price']:.2f} ({snap['change_pct']:+.2f}%)")
            lines.append("")

    if ranked["conditional"]:
        lines.append("### 条件观察")
        lines.append("")
        for s in ranked["conditional"]:
            cv2 = s["computed_v2"]
            da = cv2["dual_axis"]
            lines.append(f"- **{s['code']} {s['name']}** | {da['op_detail']} | 风险: {da['tail_risk']}")
            lines.append(f"  原因: {da.get('reason_code', 'N/A')}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 量化复核 (V2)")
    lines.append("")
    for r in quant_reviews:
        lines.append(f"### {r['code']} {r['name']}: {r['verdict']}")
        lines.append("")
        for check_name, check in r["checks"].items():
            icon = "✓" if check["pass"] else "✗"
            lines.append(f"- {icon} **{check_name}**: {check['value']} (阈值: {check['threshold']})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 审计追踪")
    lines.append("")
    audit = data.get("audit", {})
    metrics = audit.get("metrics", {})
    lines.append(f"- 总决策数: {audit.get('total_decisions', 0)}")
    if metrics:
        for cat, count in metrics.get("by_category", {}).items():
            lines.append(f"- {cat}: {count}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*报告由 T1 全生命周期策略分析引擎自动生成 (MarketBase V2)*")
    lines.append(f"*策略版本: {meta.get('strategy_profile_id', 'N/A')} v{meta.get('strategy_version', 'N/A')}*")
    lines.append("")
    lines.append("> ⚠️ 风险提示：以上为策略分析，不构成投资建议。市场有风险，投资需谨慎。")
    return "\n".join(lines)


# ============================================================================
# 运行入口
# ============================================================================

def run_analysis(
    *,
    data_root: Optional[Path] = None,
    input_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    use_v2: bool = False,
) -> int:
    """主入口：读取 T1 快照数据，运行分析，生成报告"""
    if use_v2:
        return run_analysis_v2(
            data_root=data_root,
            input_path=input_path,
            output_path=output_path,
        )

    input_path = input_path or DEFAULT_INPUT
    output_path = output_path or DEFAULT_OUTPUT

    if not input_path.exists():
        print(f"[ERROR] 找不到 T1 快照数据: {input_path}")
        print("  请先运行: python local_workflow.py build-t1-snapshot")
        return 1

    print("=" * 60)
    print("T1 Strategy — 端到端分析 (MarketBase 集成)")
    print("=" * 60)

    print("\n[1/4] 加载 t1_processed_data.json...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  加载了 {len(data['stocks'])} 只股票")

    print("\n[2/4] a-share-t1-rank-sandbox: 排序筛选...")
    ranked = rank_candidates(data["stocks"])
    print(f"  正式候选: {len(ranked['candidates'])} 只")

    print("\n[3/4] a-share-quant-sandbox: 量化复核...")
    quant_reviews = quant_review(ranked["candidates"])

    print("\n[4/4] a-share-technical-lab: 技术面研判...")
    tech_reviews = technical_review(ranked["candidates"])

    report = generate_report(data, ranked, quant_reviews, tech_reviews)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[OK] 报告: {output_path} ({output_path.stat().st_size:,} bytes)")
    return 0


def run_analysis_v2(
    *,
    data_root: Optional[Path] = None,
    input_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> int:
    """V2 主入口：使用 strategy_lifecycle 模块的确定性结论"""
    input_path = input_path or DEFAULT_INPUT_V2
    output_path = output_path or DEFAULT_OUTPUT_V2

    if not input_path.exists():
        print(f"[ERROR] 找不到 V2 快照数据: {input_path}")
        print("  请先运行: python local_workflow.py build-t1-snapshot --v2")
        return 1

    print("=" * 60)
    print("T1 全生命周期策略分析 (V2)")
    print("=" * 60)

    print("\n[1/3] 加载 t1_processed_data_v2.json...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  加载了 {len(data['stocks'])} 只股票")

    print("\n[2/3] 筛选候选（读取 Python 层确定性双轴结论）...")
    ranked = rank_candidates_v2(data["stocks"])
    print(f"  正式候选: {len(ranked['candidates'])} 只")
    print(f"  条件观察: {len(ranked['conditional'])} 只")
    print(f"  已拒绝: {len(ranked['rejected'])} 只")

    print("\n[3/3] 量化复核（读取 Python 层确定性计算结果）...")
    quant_reviews = quant_review_v2(ranked["candidates"])
    for r in quant_reviews:
        print(f"  {r['code']} {r['name']}: {r['verdict']}")

    report = generate_report_v2(data, ranked, quant_reviews)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[OK] 报告: {output_path} ({output_path.stat().st_size:,} bytes)")
    return 0


def main(argv=None):
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description="T1 Strategy Analysis")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--v2", action="store_true", help="使用 V2 全生命周期模块")
    args = parser.parse_args(argv)

    return run_analysis(
        input_path=args.input,
        output_path=args.output,
        use_v2=args.v2,
    )


if __name__ == "__main__":
    sys.exit(main())
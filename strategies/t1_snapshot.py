"""
T1 Snapshot — 从每日运行数据中提取自选池快照
==============================================
作为 MarketBase 子模块，读取 watchlist.json，从 latest daily_run
提取 snapshot + daily 数据，计算 T1 策略所需指标。

V1: 输出 t1_processed_data.json（旧版，向后兼容）
V2: 输出 t1_processed_data_v2.json（新版，使用 strategy_lifecycle 模块）

用法:
    python -m marketbase.t1_snapshot --watchlist strategies/watchlist.json
    python -m marketbase.t1_snapshot --watchlist strategies/watchlist.json --v2
    python local_workflow.py build-t1-snapshot
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

TZ_SHANGHAI = timezone(timedelta(hours=8))

DEFAULT_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "daily_runs"
DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "strategies" / "watchlist.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "strategies" / "t1_processed_data.json"
DEFAULT_OUTPUT_V2 = Path(__file__).resolve().parent.parent / "strategies" / "t1_processed_data_v2.json"


# ============================================================================
# V1 辅助函数（保留向后兼容）
# ============================================================================

def _find_latest_run(data_root: Path) -> Optional[Path]:
    """找到最新的 daily_run 目录（必须有 market_snapshot.csv）"""
    if not data_root.exists():
        return None
    dates = sorted([d for d in data_root.iterdir() if d.is_dir()], reverse=True)
    for date_dir in dates:
        runs = sorted(
            [d for d in date_dir.iterdir() if d.is_dir() and "objective_data" in d.name],
            reverse=True,
        )
        for run_dir in runs:
            if (run_dir / "market_snapshot.csv").exists():
                return run_dir
    return None


def _normalize_code(code: str) -> int:
    return int(code)


def _load_watchlist(watchlist_path: Path) -> dict:
    with open(watchlist_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _load_snapshot(run_dir: Path, codes: set[int]) -> pd.DataFrame:
    snap_path = run_dir / "market_snapshot.csv"
    if not snap_path.exists():
        raise FileNotFoundError(f"snapshot not found: {snap_path}")
    df = pd.read_csv(snap_path)
    return df[df["code"].isin(codes)]


def _load_daily(run_dir: Path, codes: set[int]) -> pd.DataFrame:
    daily_path = run_dir / "daily_indicators.csv"
    if not daily_path.exists():
        raise FileNotFoundError(f"daily indicators not found: {daily_path}")
    df = pd.read_csv(daily_path)
    return df[df["code"].isin(codes)]


def _load_market_breadth(run_dir: Path) -> dict:
    mb_path = run_dir / "market_breadth.json"
    if not mb_path.exists():
        return {}
    with open(mb_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_audit(run_dir: Path) -> dict:
    audit_path = run_dir / "data_audit.json"
    if not audit_path.exists():
        return {}
    with open(audit_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# V1 计算（向后兼容）
# ============================================================================

def _compute_t1_indicators(snap_row: pd.Series, daily_row: pd.Series) -> dict:
    """基于快照和日线数据计算 T1 策略所需指标（V1 旧版）"""
    price = float(snap_row.get("price", 0) or 0)
    high = float(snap_row.get("high", 0) or 0)
    low = float(snap_row.get("low", 0) or 0)
    change_pct = float(snap_row.get("change_pct", 0) or 0)
    volume_ratio = float(snap_row.get("volume_ratio", 0) or 0)
    turnover_rate = float(snap_row.get("turnover_rate", 0) or 0)

    ma5 = float(daily_row.get("ma5", 0) or 0)
    ma10 = float(daily_row.get("ma10", 0) or 0)
    ma20 = float(daily_row.get("ma20", 0) or 0)
    ma60 = float(daily_row.get("ma60", 0) or 0)
    rsi14 = float(daily_row.get("rsi14", 0) or 0)
    atr14 = float(daily_row.get("atr14", 0) or 0)
    atr14_pct = float(daily_row.get("atr14_pct", 0) or 0)
    boll_upper = float(daily_row.get("boll_upper", 0) or 0)
    boll_middle = float(daily_row.get("boll_middle", 0) or 0)
    boll_lower = float(daily_row.get("boll_lower", 0) or 0)
    return_5d = float(daily_row.get("return_5d", 0) or 0)
    return_20d = float(daily_row.get("return_20d", 0) or 0)
    rps20 = float(daily_row.get("rps20", 0) or 0)

    trend_aligned = bool(ma5 > 0 and ma5 > ma10 > ma20 > ma60)

    buy_lower = min(ma5, boll_middle) if boll_middle > 0 else ma5
    buy_upper = max(ma5, boll_middle) if boll_middle > 0 else ma10
    if buy_lower > buy_upper:
        buy_lower, buy_upper = buy_upper, buy_lower

    sell_lower = boll_upper if boll_upper > 0 else price * 1.05
    sell_upper = high if high > sell_lower else sell_lower * 1.02
    sell_center = (sell_lower + sell_upper) / 2

    protection = min(ma20, boll_lower) if boll_lower > 0 and ma20 > 0 else price * 0.95

    op_score = 0
    op_details = []
    if trend_aligned:
        op_score += 30
        op_details.append("趋势多头")
    else:
        op_details.append("趋势未多头")
    if 40 <= rsi14 <= 70:
        op_score += 20
        op_details.append(f"RSI健康({rsi14:.0f})")
    elif rsi14 < 30:
        op_details.append(f"RSI超卖({rsi14:.0f})")
    elif rsi14 > 80:
        op_details.append(f"RSI超买({rsi14:.0f})")
    if volume_ratio > 1.5:
        op_score += 15
        op_details.append(f"放量({volume_ratio:.1f}x)")
    elif volume_ratio > 0.8:
        op_score += 5
        op_details.append(f"量正常({volume_ratio:.1f}x)")
    else:
        op_details.append(f"缩量({volume_ratio:.1f}x)")
    if 2 <= change_pct <= 7:
        op_score += 15
        op_details.append(f"涨幅适中({change_pct:+.1f}%)")
    elif 0 < change_pct < 2:
        op_score += 5
        op_details.append(f"微涨({change_pct:+.1f}%)")
    elif change_pct > 7:
        op_details.append(f"涨幅过大({change_pct:+.1f}%)")
    if rps20 > 0:
        if rps20 >= 80:
            op_score += 10
            op_details.append(f"RPS强势({rps20:.0f})")
        elif rps20 >= 50:
            op_score += 5
            op_details.append(f"RPS中等({rps20:.0f})")
    if return_5d > 0:
        op_score += 5
        op_details.append("5日正收益")
    if return_20d > 0:
        op_score += 5
        op_details.append("20日正收益")

    if op_score >= 60:
        opportunity_quality = "strong"
    elif op_score >= 40:
        opportunity_quality = "moderate"
    elif op_score >= 20:
        opportunity_quality = "weak"
    else:
        opportunity_quality = "poor"

    tr_count = 0
    if rsi14 > 80:
        tr_count += 1
    if change_pct > 9:
        tr_count += 1
    if price < ma20 and ma20 > 0:
        tr_count += 1
    if turnover_rate > 20:
        tr_count += 1

    if tr_count >= 3:
        tail_risk = "high"
    elif tr_count >= 1:
        tail_risk = "medium"
    else:
        tail_risk = "low"

    if opportunity_quality in ("strong", "moderate") and tail_risk in ("low", "medium"):
        dual_axis_decision = "can_enter_candidate"
    elif opportunity_quality in ("strong", "moderate") and tail_risk == "high":
        dual_axis_decision = "conditional_watch"
    else:
        dual_axis_decision = "reject"

    if high > 0 and low > 0 and price > 0:
        vwap_approx = (high + low + price) / 3
        vwap_position = price / vwap_approx if vwap_approx > 0 else 1.0
    else:
        vwap_position = 1.0

    industry_sync_ok = change_pct > 0
    industry_sync_detail = "行业同步(涨幅>0)" if industry_sync_ok else "行业未同步(涨幅<=0)"

    return {
        "dual_axis_decision": dual_axis_decision,
        "op_score": op_score,
        "opportunity_quality": opportunity_quality,
        "tail_risk": tail_risk,
        "tr_count": tr_count,
        "op_detail": ", ".join(op_details),
        "buy_zone": {
            "first_lower": round(buy_lower, 2),
            "first_upper": round(buy_upper, 2),
        },
        "sell_zone": {
            "first_lower": round(sell_lower, 2),
            "first_upper": round(sell_upper, 2),
            "first_center": round(sell_center, 2),
        },
        "protection_price": round(protection, 2),
        "trend_aligned": trend_aligned,
        "vwap_position": round(vwap_position, 4),
        "industry_sync_ok": industry_sync_ok,
        "industry_sync_detail": industry_sync_detail,
        "atr14": round(atr14, 2),
        "atr14_pct": round(atr14_pct, 2),
    }


# ============================================================================
# V2 计算（使用 strategy_lifecycle 模块 + ObjectiveDataProvider）
# ============================================================================

def _compute_t1_indicators_v2(
    snap_row: pd.Series,
    daily_row: pd.Series,
    market_breadth: dict,
    industry_data: Optional[dict] = None,
    minute_data: Optional[dict] = None,
    pivots_data: Optional[dict] = None,
) -> dict:
    """基于新模块计算 T1 策略全生命周期指标（V2）
    
    minute_data: 来自 intraday.py 的分钟级 VWAP 数据
        {code: {full_day_vwap: float, afternoon_avwap: float, ...}}
    industry_data: 来自 market_breadth.py 的行业数据
        {code: {advance_ratio: float, equal_weight_return: float, rank: int, ...}}
    pivots_data: 来自 intraday.py 的摆动点数据
        {code: {afternoon_swing_low: float, named_pivot: float, ...}}
    """
    import math

    from marketbase.objective_data_provider import ObjectiveDataProvider
    from strategies.strategy_lifecycle.channel_identifier import ChannelIdentifier
    from strategies.strategy_lifecycle.dual_axis import DualAxis
    from strategies.strategy_lifecycle.entry_state_machine import EntryStateMachine, EntryState
    from strategies.strategy_lifecycle.audit import AuditTrail, DecisionRecord, AuditCategory

    provider = ObjectiveDataProvider

    # ── 提取基础数据 ──
    price = float(snap_row.get("price", 0) or 0)
    pre_close = float(snap_row.get("pre_close", 0) or 0)
    open_price = float(snap_row.get("open", 0) or 0)
    high = float(snap_row.get("high", 0) or 0)
    low = float(snap_row.get("low", 0) or 0)
    change_pct = float(snap_row.get("change_pct", 0) or 0)
    volume_ratio = float(snap_row.get("volume_ratio", 0) or 0)
    turnover_rate = float(snap_row.get("turnover_rate", 0) or 0)
    code = str(int(snap_row.get("code", 0))).zfill(6)
    name = str(snap_row.get("name", ""))
    industry = str(snap_row.get("industry", ""))
    board = str(snap_row.get("board", ""))
    is_st = bool(snap_row.get("is_st", False))
    is_suspended = bool(snap_row.get("is_suspended", False))
    total_mv = float(snap_row.get("total_mv", 0) or 0)
    circ_mv = float(snap_row.get("circ_mv", 0) or 0)

    ma5 = float(daily_row.get("ma5", 0) or 0)
    ma10 = float(daily_row.get("ma10", 0) or 0)
    ma20 = float(daily_row.get("ma20", 0) or 0)
    ma60 = float(daily_row.get("ma60", 0) or 0)
    ma120 = float(daily_row.get("ma120", 0) or 0)
    ma250 = float(daily_row.get("ma250", 0) or 0)
    rsi14 = float(daily_row.get("rsi14", 0) or 0)
    atr14 = float(daily_row.get("atr14", 0) or 0)
    atr14_pct = float(daily_row.get("atr14_pct", 0) or 0)
    boll_upper = float(daily_row.get("boll_upper", 0) or 0)
    boll_middle = float(daily_row.get("boll_middle", 0) or 0)
    boll_lower = float(daily_row.get("boll_lower", 0) or 0)
    return_5d = float(daily_row.get("return_5d", 0) or 0)
    return_20d = float(daily_row.get("return_20d", 0) or 0)
    rps20 = float(daily_row.get("rps20", 0) or 0)

    # ── 构建 DailyContext ──
    daily_ctx = provider.build_daily_context(
        code=code, name=name,
        price=price, pre_close=pre_close, open_price=open_price,
        high=high, low=low, change_pct=change_pct,
        volume=float(snap_row.get("volume", 0) or 0),
        amount=float(snap_row.get("amount", 0) or 0),
        turnover_rate=turnover_rate, volume_ratio=volume_ratio,
        ma5=ma5, ma10=ma10, ma20=ma20, ma60=ma60, ma120=ma120, ma250=ma250,
        rsi14=rsi14, atr14=atr14, atr14_pct=atr14_pct,
        boll_upper=boll_upper, boll_middle=boll_middle, boll_lower=boll_lower,
        return_5d=return_5d, return_20d=return_20d, rps20=rps20,
        industry=industry, board=board,
        is_st=is_st, is_suspended=is_suspended,
        total_mv=total_mv, circ_mv=circ_mv,
    )

    boll_position = daily_ctx.boll_position

    # ── 上影线分析 ──
    # 使用当日 OHLC 数据近似前复权值
    shadow = provider.compute_upper_shadow(
        adjusted_high=high, adjusted_low=low,
        adjusted_open=open_price, adjusted_close=price,
    )

    # ── 行业同步判断 ──
    industry_sync_pass = False
    industry_advance_ratio = 0.0

    # 优先使用行业数据（来自 market_breadth.py），否则回退到个股涨幅
    stock_industry_data = None
    if industry_data and code in industry_data:
        stock_industry_data = industry_data[code]
    elif industry_data and industry in industry_data:
        stock_industry_data = industry_data[industry]

    if stock_industry_data:
        industry_advance_ratio = stock_industry_data.get("advance_ratio", 0.0)
        industry_eq_return = stock_industry_data.get("equal_weight_return", 0.0)
        valid_count = stock_industry_data.get("valid_count", 0)
        industry_rank = stock_industry_data.get("rank", 999)
        total_industries = stock_industry_data.get("total_industries", 1)

        if valid_count >= 20:
            industry_sync_pass = (
                (industry_advance_ratio >= 0.50 and industry_eq_return > 0)
                or industry_rank <= math.ceil(total_industries * 0.30)
            )
            industry_sync_detail = (
                f"行业同步(广度{industry_advance_ratio:.1%}, 等权{industry_eq_return:+.2%})"
                if industry_sync_pass
                else f"行业未同步(广度{industry_advance_ratio:.1%}, 排名{industry_rank}/{total_industries})"
            )
        else:
            industry_sync_detail = f"行业数据不足(有效成分{valid_count}<20)"
    else:
        # 回退：用个股涨幅作为近似
        industry_sync_detail = "行业同步(涨幅>0)" if change_pct > 0 else "行业未同步(涨幅<=0)"
        industry_sync_pass = change_pct > 0

    # ── 分钟数据（VWAP） ──
    full_day_vwap = None
    afternoon_avwap = None
    if minute_data and code in minute_data:
        md = minute_data[code]
        full_day_vwap = md.get("full_day_vwap")
        afternoon_avwap = md.get("afternoon_avwap")

    # ── 通道识别 ──
    # 使用近似 MACD 动量（因为 daily_indicators 不直接提供 MACD histogram）
    momentum_delta_1 = 0.02 if rsi14 > 50 else -0.02
    momentum_delta_3 = 0.05 if rps20 > 70 else -0.05

    channel_results = ChannelIdentifier.identify_all(
        adjusted_close=price,
        ma11=ma10 if ma10 > 0 else price,
        ma23=ma20 if ma20 > 0 else price,
        rsi14=rsi14,
        rps20=rps20,
        momentum_delta_1=momentum_delta_1,
        momentum_delta_3=momentum_delta_3,
        boll_position=boll_position,
        return_20d=return_20d,
        atr14=atr14,
        atr14_pct=atr14_pct,
        repeated_upper_shadow=shadow.get("long_upper_shadow", False),
        full_day_vwap=full_day_vwap,
        afternoon_avwap=afternoon_avwap,
        pre_reclaim_low=low if low > 0 else None,
        pullback_vwap_frozen=full_day_vwap,  # 通道所需 VWAP 参考
        tick_size=0.01,
        high_momentum_mode="research_only",
    )

    # 找到通过的主通道
    primary_channel = None
    for cr in channel_results:
        if cr.passed and cr.production_buyable:
            primary_channel = cr
            break

    # ── 双轴判断 ──
    market_veto = False
    fm = market_breadth.get("full_market", {})
    advance_ratio = fm.get("advance_count", 0) / max(fm.get("total", 1), 1)
    if advance_ratio < 0.35:
        market_veto = True

    dual_result = DualAxis.evaluate(
        trend_aligned=daily_ctx.trend_aligned,
        rsi14=rsi14,
        volume_ratio=volume_ratio,
        change_pct=change_pct,
        rps20=rps20,
        return_5d=return_5d,
        return_20d=return_20d,
        industry_sync_pass=industry_sync_pass,
        channel_type=primary_channel.channel.value if primary_channel else "unknown",
        price=price,
        ma20=ma20,
        turnover_rate=turnover_rate,
        atr14_pct=atr14_pct,
        repeated_upper_shadow=shadow.get("long_upper_shadow", False),
        long_upper_shadow=shadow.get("long_upper_shadow", False),
        market_veto=market_veto,
        protection_constructible=True,
        sell_zone_constructible=True,
    )

    # ── 午后摆动低点（保护位结构支撑） ──
    afternoon_swing_low = None
    named_pivot = None
    if pivots_data and code in pivots_data:
        pv = pivots_data[code]
        afternoon_swing_low = pv.get("afternoon_swing_low")
        named_pivot = pv.get("named_pivot", high)

    # ── 通道特定 trigger_reference（附录 A.3） ──
    trigger_reference = price * 0.998  # 默认回退
    if primary_channel:
        ch = primary_channel.channel
        if ch == ChannelType.TREND_CONTINUATION:
            candidates = [v for v in [full_day_vwap, afternoon_avwap, named_pivot] if v is not None]
            if candidates:
                trigger_reference = max(candidates)
        elif ch == ChannelType.STRONG_PULLBACK_RECLAIM:
            reclaim_pivot = named_pivot
            candidates = [v for v in [full_day_vwap, reclaim_pivot] if v is not None]
            if candidates:
                trigger_reference = max(candidates)
        elif ch == ChannelType.SECTOR_REVERSAL_CHALLENGER:
            candidates = [v for v in [full_day_vwap, named_pivot] if v is not None]
            if candidates:
                trigger_reference = max(candidates)

    # ── 保护位结构支撑（附录 A.4） ──
    structural_support = max(ma20, boll_lower) if boll_lower > 0 and ma20 > 0 else price * 0.95
    if afternoon_swing_low is not None and afternoon_swing_low > 0:
        # 优先使用午后摆动低点
        structural_support = max(afternoon_swing_low, structural_support)

    # ── 买区/卖区（附录 A.3/A.4） ──
    buy_zone = provider.compute_buy_zone(
        trigger_reference=trigger_reference,
        confirmation_close=price,
        atr14=atr14,
        tick_size=0.01,
    )

    sell_zones = provider.compute_sell_zones(
        buy_zone_upper=buy_zone["buy_zone_upper"],
        atr14=atr14,
        tick_size=0.01,
    )

    protection = provider.compute_protection(
        buy_zone_lower=buy_zone["buy_zone_lower"],
        buy_zone_upper=buy_zone["buy_zone_upper"],
        atr14=atr14,
        structural_support=structural_support,
        tick_size=0.01,
    )

    # ── 手续费后经济性 ──
    fees = provider.compute_fees(
        buy_price=buy_zone["buy_zone_upper"],
        sell_price=sell_zones.get("first_zone_lower", price * 1.05),
        quantity=100,
    )

    # ── 入场状态机初始化 ──
    strategy_profile_id = "watchlist_t1_v1"
    strategy_version = "2.0.0-dev"
    decision_id = str(uuid.uuid4())[:12]

    state_machine = EntryStateMachine(code, strategy_profile_id, strategy_version)

    # 初始评估
    if is_st or is_suspended:
        try:
            state_machine.to_unassessed_to_rejected(decision_id, {"reason": "st_or_suspended"})
        except Exception:
            pass
    elif primary_channel:
        try:
            state_machine.to_unassessed_to_deep_watch(
                decision_id, {"channel": primary_channel.channel.value}
            )
        except Exception:
            pass
    else:
        try:
            state_machine.to_unassessed_to_rejected(
                decision_id, {"reason": "no_channel_pass"}
            )
        except Exception:
            pass

    return {
        "formula_version": "2.0.0",
        "decision_id": decision_id,
        "strategy_profile_id": strategy_profile_id,
        "strategy_version": strategy_version,
        # 通道识别
        "channels": {
            cr.channel.value: {
                "passed": cr.passed,
                "production_buyable": cr.production_buyable,
                "reason": cr.reason,
                "evidence": cr.evidence,
            }
            for cr in channel_results
        },
        "primary_channel": primary_channel.channel.value if primary_channel else None,
        # 双轴判断
        "dual_axis": {
            "decision": dual_result.decision.value,
            "opportunity_quality": dual_result.opportunity_quality.value,
            "tail_risk": dual_result.tail_risk.value,
            "op_score": dual_result.op_score,
            "op_detail": dual_result.op_detail,
            "tr_count": dual_result.tr_count,
            "tr_flags": dual_result.tr_flags,
            "reason_code": dual_result.reason_code,
        },
        # 附录 A 买区/卖区/保护位
        "buy_zone": {
            "lower": buy_zone["buy_zone_lower"],
            "upper": buy_zone["buy_zone_upper"],
            "no_chase_price": buy_zone["no_chase_price"],
            "trigger_reference": buy_zone["trigger_reference"],
            "empty": buy_zone["empty"],
            "confirmation_above_no_chase": buy_zone["confirmation_above_no_chase"],
        },
        "sell_zone": {
            "first_lower": sell_zones.get("first_zone_lower"),
            "first_upper": sell_zones.get("first_zone_upper"),
            "first_center": sell_zones.get("first_zone_center"),
            "second_lower": sell_zones.get("second_zone_lower"),
            "second_upper": sell_zones.get("second_zone_upper"),
            "second_center": sell_zones.get("second_zone_center"),
            "second_available": sell_zones.get("second_zone") != "not_available",
            "error": sell_zones.get("error"),
        },
        "protection": {
            "price": protection["protection_price"],
            "constructible": protection["constructible"],
            "structural_support": protection["structural_support"],
            "reason": protection["reason"],
        },
        "fees": fees,
        # 上影线
        "upper_shadow": shadow,
        # 行业同步
        "industry_sync": {
            "pass": industry_sync_pass,
            "detail": industry_sync_detail,
            "advance_ratio": industry_advance_ratio,
        },
        # 市场环境
        "market": {
            "veto": market_veto,
            "advance_ratio": advance_ratio,
            "broad_market_detail": fm.get("advance_count", 0),
        },
        # 摆动点与结构位
        "pivots": {
            "afternoon_swing_low": afternoon_swing_low,
            "named_pivot": named_pivot,
            "structural_support": structural_support,
            "trigger_reference": trigger_reference,
        },
        # early_target_reversal（开盘冲高回落检测）
        "early_target_reversal": {
            "applies": False,  # 需要分钟级实时数据，盘后快照不适用
            "note": "requires_intraday_minute_data",
        },
        # 入场状态
        "entry_state": state_machine.state.value,
        "entry_state_history": [
            {
                "from": t.from_state.value,
                "to": t.to_state.value,
                "decision_id": t.decision_id,
                "reason_code": t.reason_code,
            }
            for t in state_machine.history
        ],
        # 日线上下文
        "daily_context": {
            "trend_aligned": daily_ctx.trend_aligned,
            "boll_position": boll_position,
            "rsi14": rsi14,
            "atr14": atr14,
            "atr14_pct": atr14_pct,
            "rps20": rps20,
            "return_5d": return_5d,
            "return_20d": return_20d,
        },
    }


# ============================================================================
# V1 构建（向后兼容）
# ============================================================================

def build_t1_snapshot(
    *,
    data_root: Optional[Path] = None,
    watchlist_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> int:
    """主入口：从最新 daily_run 提取自选池数据，计算 T1 指标，输出 JSON"""
    data_root = data_root or DEFAULT_DATA_ROOT
    watchlist_path = watchlist_path or DEFAULT_WATCHLIST
    output_path = output_path or DEFAULT_OUTPUT

    if not watchlist_path.exists():
        print(f"[ERROR] 自选池文件不存在: {watchlist_path}")
        return 1

    wl = _load_watchlist(watchlist_path)
    watch_codes = {_normalize_code(s["code"]) for s in wl.get("stocks", [])}
    print(f"[1/5] 自选池: {len(watch_codes)} 只股票")

    run_dir = _find_latest_run(data_root)
    if run_dir is None:
        print("[ERROR] 找不到 daily_run 数据，请先运行: python local_workflow.py collect")
        return 1
    print(f"[2/5] 最新 run: {run_dir}")

    print("[3/5] 加载 snapshot + daily + audit...")
    snap_df = _load_snapshot(run_dir, watch_codes)
    daily_df = _load_daily(run_dir, watch_codes)
    audit = _load_audit(run_dir)
    market_breadth = _load_market_breadth(run_dir)

    found = len(snap_df)
    missing = watch_codes - set(snap_df["code"].tolist())
    print(f"  快照: {found}/{len(watch_codes)} 只匹配")
    if missing:
        print(f"  未匹配: {missing}")

    print("[4/5] 计算 T1 策略指标...")
    stocks = []
    for _, snap_row in snap_df.iterrows():
        code = int(snap_row["code"])
        name = snap_row.get("name", "")

        daily_match = daily_df[daily_df["code"] == code]
        if daily_match.empty:
            print(f"  [WARN] {code} 缺少日线指标，跳过")
            continue
        daily_row = daily_match.iloc[0]

        computed = _compute_t1_indicators(snap_row, daily_row)

        stocks.append({
            "code": str(code).zfill(6),
            "name": str(name),
            "snapshot": {
                "price": float(snap_row.get("price", 0) or 0),
                "pre_close": float(snap_row.get("pre_close", 0) or 0),
                "open": float(snap_row.get("open", 0) or 0),
                "high": float(snap_row.get("high", 0) or 0),
                "low": float(snap_row.get("low", 0) or 0),
                "change_pct": float(snap_row.get("change_pct", 0) or 0),
                "volume": float(snap_row.get("volume", 0) or 0),
                "amount": float(snap_row.get("amount", 0) or 0),
                "turnover_rate": float(snap_row.get("turnover_rate", 0) or 0),
                "volume_ratio": float(snap_row.get("volume_ratio", 0) or 0),
                "total_mv": float(snap_row.get("total_mv", 0) or 0),
                "circ_mv": float(snap_row.get("circ_mv", 0) or 0),
                "pe_ratio": float(snap_row.get("pe_ratio", 0) or 0),
                "pb_ratio": float(snap_row.get("pb_ratio", 0) or 0),
                "industry": str(snap_row.get("industry", "")),
                "board": str(snap_row.get("board", "")),
                "is_st": bool(snap_row.get("is_st", False)),
                "is_suspended": bool(snap_row.get("is_suspended", False)),
            },
            "daily": {
                "ma5": float(daily_row.get("ma5", 0) or 0),
                "ma10": float(daily_row.get("ma10", 0) or 0),
                "ma20": float(daily_row.get("ma20", 0) or 0),
                "ma60": float(daily_row.get("ma60", 0) or 0),
                "ma120": float(daily_row.get("ma120", 0) or 0),
                "ma250": float(daily_row.get("ma250", 0) or 0),
                "rsi14": float(daily_row.get("rsi14", 0) or 0),
                "atr14": float(daily_row.get("atr14", 0) or 0),
                "atr14_pct": float(daily_row.get("atr14_pct", 0) or 0),
                "boll_upper": float(daily_row.get("boll_upper", 0) or 0),
                "boll_middle": float(daily_row.get("boll_middle", 0) or 0),
                "boll_lower": float(daily_row.get("boll_lower", 0) or 0),
                "return_5d": float(daily_row.get("return_5d", 0) or 0),
                "return_10d": float(daily_row.get("return_10d", 0) or 0),
                "return_20d": float(daily_row.get("return_20d", 0) or 0),
                "rps20": float(daily_row.get("rps20", 0) or 0),
            },
            "computed": computed,
        })

    print(f"  成功计算: {len(stocks)} 只")

    print("[5/5] 输出 t1_processed_data.json...")
    output = {
        "meta": {
            "source_run": str(run_dir.name),
            "source_generated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "formula_version": "1.0.0",
            "quality_status": audit.get("quality_status", "unknown"),
            "strategy_profile_id": wl.get("strategy_profile_id", "unknown"),
            "strategy_version": wl.get("strategy_version", "unknown"),
        },
        "market": {
            "full_market": market_breadth.get("full_market", {}),
            "advance_ratio": market_breadth.get("full_market", {}).get("advance_count", 0)
            / max(market_breadth.get("full_market", {}).get("total", 1), 1),
        },
        "stocks": stocks,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[OK] 输出: {output_path} ({output_path.stat().st_size:,} bytes)")
    return 0


# ============================================================================
# V2 构建（使用新模块）
# ============================================================================

def build_t1_snapshot_v2(
    *,
    data_root: Optional[Path] = None,
    watchlist_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    industry_data: Optional[dict] = None,
    minute_data: Optional[dict] = None,
) -> int:
    """
    V2 主入口：使用 strategy_lifecycle 模块的完整计算管线

    输出 t1_processed_data_v2.json，包含通道识别、双轴判断、
    状态机、附录A公式、审计追踪、候选名额管理等完整字段。

    自动尝试加载：
    - industry_ma_distribution.json → 行业数据
    - intraday_1m.parquet → 分钟数据（如果存在）
    """
    from strategies.strategy_lifecycle.audit import AuditTrail, DecisionRecord
    from strategies.strategy_lifecycle.candidate_slot import CandidateSlotManager
    from strategies.strategy_lifecycle.debug_logger import DebugLogManager
    from datetime import date

    data_root = data_root or DEFAULT_DATA_ROOT
    watchlist_path = watchlist_path or DEFAULT_WATCHLIST
    output_path = output_path or DEFAULT_OUTPUT_V2

    if not watchlist_path.exists():
        print(f"[ERROR] 自选池文件不存在: {watchlist_path}")
        return 1

    wl = _load_watchlist(watchlist_path)
    watch_codes = {_normalize_code(s["code"]) for s in wl.get("stocks", [])}
    strategy_profile_id = wl.get("strategy_profile_id", "watchlist_t1_v1")
    strategy_version = wl.get("strategy_version", "2.0.0-dev")
    print(f"[1/6] 自选池: {len(watch_codes)} 只股票 ({strategy_profile_id})")

    run_dir = _find_latest_run(data_root)
    if run_dir is None:
        print("[ERROR] 找不到 daily_run 数据，请先运行: python local_workflow.py collect")
        return 1
    print(f"[2/6] 最新 run: {run_dir}")

    print("[3/6] 加载 snapshot + daily + market_breadth + audit...")
    snap_df = _load_snapshot(run_dir, watch_codes)
    daily_df = _load_daily(run_dir, watch_codes)
    audit = _load_audit(run_dir)
    market_breadth = _load_market_breadth(run_dir)

    # 尝试加载行业和分钟数据
    industry_data_loaded = industry_data
    if industry_data_loaded is None:
        industry_ma_path = run_dir / "industry_ma_distribution.json"
        if industry_ma_path.exists():
            with open(industry_ma_path, "r", encoding="utf-8") as f:
                industry_data_loaded = json.load(f)
            print(f"  行业数据: {len(industry_data_loaded)} 个行业")
        else:
            print("  行业数据: 未找到 industry_ma_distribution.json，使用个股涨幅回退")

    minute_data_loaded = minute_data
    if minute_data_loaded is None:
        # 尝试从 intraday parquet 加载
        intraday_path = run_dir / "intraday_1m.parquet"
        if intraday_path.exists():
            try:
                import pandas as pd
                intraday_df = pd.read_parquet(intraday_path)
                # 构建分钟数据字典
                minute_data_loaded = {}
                for c in watch_codes:
                    code_df = intraday_df[intraday_df["code"] == c]
                    if not code_df.empty:
                        total_amt = code_df["amount"].sum()
                        total_vol = code_df["volume"].sum()
                        vwap = total_amt / total_vol if total_vol > 0 else None
                        afternoon = code_df[code_df["time"] >= "13:00"]
                        afternoon_amt = afternoon["amount"].sum() if not afternoon.empty else 0
                        afternoon_vol = afternoon["volume"].sum() if not afternoon.empty else 0
                        avwap = afternoon_amt / afternoon_vol if afternoon_vol > 0 else None
                        minute_data_loaded[c] = {
                            "full_day_vwap": round(vwap, 4) if vwap else None,
                            "afternoon_avwap": round(avwap, 4) if avwap else None,
                        }
                print(f"  分钟数据: {len(minute_data_loaded)}/{len(watch_codes)} 只")
            except Exception as e:
                print(f"  分钟数据: 加载失败 ({e})，使用日线近似")
                minute_data_loaded = None
        else:
            print("  分钟数据: intraday_1m.parquet 不存在，使用日线近似")

    # 尝试加载摆动点数据
    pivots_data_loaded = None
    if minute_data_loaded is not None:
        try:
            from marketbase.intraday import find_named_pivots, find_afternoon_swing_low
            import pandas as pd
            intraday_path = run_dir / "intraday_1m.parquet"
            if intraday_path.exists():
                intraday_df = pd.read_parquet(intraday_path)
                pivots_df = find_named_pivots(intraday_df)
                afternoon_df = find_afternoon_swing_low(pivots_df)
                pivots_data_loaded = {}
                for c in watch_codes:
                    pivots_data_loaded[c] = {"afternoon_swing_low": None, "named_pivot": None}
                for _, row in afternoon_df.iterrows():
                    c = int(row["code"])
                    if c in watch_codes:
                        pivots_data_loaded[c]["afternoon_swing_low"] = float(row["price"])
                for _, row in pivots_df.iterrows():
                    c = int(row["code"])
                    if c in watch_codes and row["pivot_type"] == "high":
                        pivots_data_loaded[c]["named_pivot"] = float(row["price"])
                print(f"  摆动点: {sum(1 for v in pivots_data_loaded.values() if v['afternoon_swing_low'])} 只有午后低点")
        except Exception as e:
            print(f"  摆动点: 加载失败 ({e})")
            pivots_data_loaded = None

    found = len(snap_df)
    missing = watch_codes - set(snap_df["code"].tolist())
    print(f"  快照: {found}/{len(watch_codes)} 只匹配")
    if missing:
        print(f"  未匹配: {missing}")

    print("[4/6] 计算 T1 策略全生命周期指标 (V2)...")
    stocks = []
    for _, snap_row in snap_df.iterrows():
        code = int(snap_row["code"])
        name = snap_row.get("name", "")

        daily_match = daily_df[daily_df["code"] == code]
        if daily_match.empty:
            print(f"  [WARN] {code} 缺少日线指标，跳过")
            continue
        daily_row = daily_match.iloc[0]

        computed_v2 = _compute_t1_indicators_v2(
            snap_row, daily_row, market_breadth, industry_data_loaded, minute_data_loaded, pivots_data_loaded
        )

        stocks.append({
            "code": str(code).zfill(6),
            "name": str(name),
            "snapshot": {
                "price": float(snap_row.get("price", 0) or 0),
                "pre_close": float(snap_row.get("pre_close", 0) or 0),
                "open": float(snap_row.get("open", 0) or 0),
                "high": float(snap_row.get("high", 0) or 0),
                "low": float(snap_row.get("low", 0) or 0),
                "change_pct": float(snap_row.get("change_pct", 0) or 0),
                "volume": float(snap_row.get("volume", 0) or 0),
                "amount": float(snap_row.get("amount", 0) or 0),
                "turnover_rate": float(snap_row.get("turnover_rate", 0) or 0),
                "volume_ratio": float(snap_row.get("volume_ratio", 0) or 0),
                "total_mv": float(snap_row.get("total_mv", 0) or 0),
                "circ_mv": float(snap_row.get("circ_mv", 0) or 0),
                "industry": str(snap_row.get("industry", "")),
                "board": str(snap_row.get("board", "")),
                "is_st": bool(snap_row.get("is_st", False)),
                "is_suspended": bool(snap_row.get("is_suspended", False)),
            },
            "daily": {
                "ma5": float(daily_row.get("ma5", 0) or 0),
                "ma10": float(daily_row.get("ma10", 0) or 0),
                "ma20": float(daily_row.get("ma20", 0) or 0),
                "ma60": float(daily_row.get("ma60", 0) or 0),
                "ma120": float(daily_row.get("ma120", 0) or 0),
                "ma250": float(daily_row.get("ma250", 0) or 0),
                "rsi14": float(daily_row.get("rsi14", 0) or 0),
                "atr14": float(daily_row.get("atr14", 0) or 0),
                "atr14_pct": float(daily_row.get("atr14_pct", 0) or 0),
                "boll_upper": float(daily_row.get("boll_upper", 0) or 0),
                "boll_middle": float(daily_row.get("boll_middle", 0) or 0),
                "boll_lower": float(daily_row.get("boll_lower", 0) or 0),
                "return_5d": float(daily_row.get("return_5d", 0) or 0),
                "return_10d": float(daily_row.get("return_10d", 0) or 0),
                "return_20d": float(daily_row.get("return_20d", 0) or 0),
                "rps20": float(daily_row.get("rps20", 0) or 0),
            },
            "computed_v2": computed_v2,
        })

    print(f"  成功计算: {len(stocks)} 只")

    # ── 汇总统计 ──
    candidate_count = sum(
        1 for s in stocks
        if s["computed_v2"]["dual_axis"]["decision"] == "can_enter_candidate"
    )
    conditional_count = sum(
        1 for s in stocks
        if s["computed_v2"]["dual_axis"]["decision"] == "conditional_watch"
    )
    rejected_count = sum(
        1 for s in stocks
        if s["computed_v2"]["dual_axis"]["decision"] == "reject"
    )
    print(f"  正式候选: {candidate_count}, 条件观察: {conditional_count}, 已拒绝: {rejected_count}")

    print("[5/6] 构建审计追踪...")
    audit_trail = AuditTrail()
    for s in stocks:
        cv2 = s["computed_v2"]
        decision_id = cv2["decision_id"]
        record = DecisionRecord(
            decision_id=decision_id,
            strategy_profile_id=strategy_profile_id,
            strategy_version=strategy_version,
            lifecycle_contract_version="1.6.0",
            schema_version="2.0.0",
            execution_rule_version="2.0.0-dev",
            skill_version="2.0.0-dev",
            code=s["code"],
            timestamp=datetime.now(),
            action="snapshot",
            state_before="unassessed",
            state_after=cv2["entry_state"],
            reason_code=cv2["dual_axis"]["reason_code"],
            evidence=cv2,
        )
        audit_trail.add_record(record)

    print("[6/6] 输出 t1_processed_data_v2.json...")
    output = {
        "meta": {
            "source_run": str(run_dir.name),
            "source_generated_at": datetime.now(TZ_SHANGHAI).isoformat(),
            "formula_version": "2.0.0",
            "quality_status": audit.get("quality_status", "unknown"),
            "strategy_profile_id": strategy_profile_id,
            "strategy_version": strategy_version,
            "lifecycle_contract_version": "1.6.0",
            "schema_version": "2.0.0",
            "execution_rule_version": "2.0.0-dev",
            "skill_version": "2.0.0-dev",
        },
        "market": {
            "full_market": market_breadth.get("full_market", {}),
            "advance_ratio": market_breadth.get("full_market", {}).get("advance_count", 0)
            / max(market_breadth.get("full_market", {}).get("total", 1), 1),
        },
        "summary": {
            "total": len(stocks),
            "candidates": candidate_count,
            "conditional": conditional_count,
            "rejected": rejected_count,
        },
        "stocks": stocks,
        "audit": {
            "total_decisions": len(audit_trail.records),
            "metrics": audit_trail.get_metrics(),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[OK] 输出: {output_path} ({output_path.stat().st_size:,} bytes)")
    print(f"  版本: {len(stocks)} 只股票, {candidate_count} 候选, {conditional_count} 条件观察")
    return 0


# ============================================================================
# CLI
# ============================================================================

def main(argv=None):
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="T1 Snapshot Builder")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--v2", action="store_true", help="使用 V2 新模块计算")
    args = parser.parse_args(argv)

    if args.v2:
        return build_t1_snapshot_v2(
            data_root=args.data_root,
            watchlist_path=args.watchlist,
            output_path=args.output or DEFAULT_OUTPUT_V2,
        )
    else:
        return build_t1_snapshot(
            data_root=args.data_root,
            watchlist_path=args.watchlist,
            output_path=args.output or DEFAULT_OUTPUT,
        )


if __name__ == "__main__":
    sys.exit(main())
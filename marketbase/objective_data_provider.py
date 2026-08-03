"""
ObjectiveDataProvider — 行情准确性的唯一事实入口
==================================================
封装 MarketBase 现有数据采集模块，输出带 as_of/available_at/Provider/
公式版本/证据ID/原始响应哈希的冻结结构化快照。

按 §8.0 设计：纯函数事实层，不依赖任何 Skill，不调用模型，
不读取自然语言结论。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

TZ_SHANGHAI = timezone(timedelta(hours=8))


class ProviderType(Enum):
    TENCENT = "tencent"
    SINA = "sina"
    EASTMONEY = "eastmoney"
    EFINANCE = "efinance"
    AKSHARE = "akshare"
    BAOSTOCK = "baostock"
    TUSHARE = "tushare"
    CACHED = "cached"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FrozenSnapshot:
    """冻结快照 — 不可变，可复现"""
    as_of: datetime
    available_at: datetime
    provider: ProviderType
    formula_version: str
    evidence_id: str
    raw_response_hash: str
    data: dict = field(default_factory=dict)

    @staticmethod
    def compute_hash(data: dict) -> str:
        """计算原始响应哈希"""
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class DailyContext:
    """日线上下文 — 冻结快照"""
    code: str
    name: str
    price: float
    pre_close: float
    open: float
    high: float
    low: float
    change_pct: float
    volume: float
    amount: float
    turnover_rate: float
    volume_ratio: float
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    ma120: float
    ma250: float
    rsi14: float
    atr14: float
    atr14_pct: float
    boll_upper: float
    boll_middle: float
    boll_lower: float
    boll_position: float
    return_5d: float
    return_20d: float
    rps20: float
    trend_aligned: bool
    industry: str = ""
    board: str = ""
    is_st: bool = False
    is_suspended: bool = False
    total_mv: float = 0.0
    circ_mv: float = 0.0


@dataclass(frozen=True)
class MinuteContext:
    """分钟上下文 — 冻结快照"""
    code: str
    confirmation_window: list[dict] = field(default_factory=list)
    full_day_vwap: Optional[float] = None
    afternoon_avwap: Optional[float] = None
    vwap_frozen_before_m1: Optional[float] = None
    activity_ratio: Optional[float] = None
    activity_hold: bool = False
    confirmation_close: Optional[float] = None
    trigger_reference: Optional[float] = None
    data_freshness_seconds: int = 0
    provider: ProviderType = ProviderType.UNKNOWN


@dataclass(frozen=True)
class MarketContext:
    """市场上下文 — 冻结快照"""
    advance_ratio: float = 0.0
    broad_market_veto: bool = False
    growth_market_veto: bool = False
    industry_advance_ratio: float = 0.0
    industry_equal_weight_return: float = 0.0
    industry_sync_pass: bool = False
    valid_industry_count: int = 0
    data_quality: str = "unknown"


@dataclass(frozen=True)
class TradePlan:
    """交易计划 — 冻结快照"""
    code: str
    buy_zone_lower: float
    buy_zone_upper: float
    no_chase_price: float
    protection_price: float
    first_zone_lower: float
    first_zone_upper: float
    first_zone_center: float
    second_zone_lower: Optional[float] = None
    second_zone_upper: Optional[float] = None
    second_zone_center: Optional[float] = None
    reward_risk_ratio: float = 0.0
    first_zone_net_reward: float = 0.0
    planned_loss: float = 0.0
    fee_policy_id: str = "default_v1"


class ObjectiveDataProvider:
    """客观数据提供者 — 事实层唯一入口"""

    FORMULA_VERSION = "2.0.0"
    FEE_POLICY = {
        "commission_buy_rate": 0.0003,
        "commission_sell_rate": 0.0003,
        "commission_min_per_order_cny": 5.0,
        "stamp_tax_sell_rate": 0.0005,
        "transfer_fee_buy_rate": 0.00001,
        "transfer_fee_sell_rate": 0.00001,
    }

    @staticmethod
    def freeze_snapshot(data: dict, provider: ProviderType = ProviderType.UNKNOWN) -> FrozenSnapshot:
        """冻结快照"""
        now = datetime.now(TZ_SHANGHAI)
        return FrozenSnapshot(
            as_of=now,
            available_at=now,
            provider=provider,
            formula_version=ObjectiveDataProvider.FORMULA_VERSION,
            evidence_id=FrozenSnapshot.compute_hash(data),
            raw_response_hash=FrozenSnapshot.compute_hash(data),
            data=data,
        )

    @staticmethod
    def build_daily_context(
        *,
        code: str,
        name: str,
        price: float,
        pre_close: float,
        open_price: float,
        high: float,
        low: float,
        change_pct: float,
        volume: float,
        amount: float,
        turnover_rate: float,
        volume_ratio: float,
        ma5: float,
        ma10: float,
        ma20: float,
        ma60: float,
        ma120: float,
        ma250: float,
        rsi14: float,
        atr14: float,
        atr14_pct: float,
        boll_upper: float,
        boll_middle: float,
        boll_lower: float,
        return_5d: float = 0.0,
        return_20d: float = 0.0,
        rps20: float = 0.0,
        industry: str = "",
        board: str = "",
        is_st: bool = False,
        is_suspended: bool = False,
        total_mv: float = 0.0,
        circ_mv: float = 0.0,
    ) -> DailyContext:
        """构建日线上下文"""
        trend_aligned = bool(ma5 > 0 and ma5 > ma10 > ma20 > ma60)
        boll_position_value = 0.5
        if boll_upper > boll_lower and boll_upper > 0:
            boll_position_value = (price - boll_lower) / (boll_upper - boll_lower)

        return DailyContext(
            code=code,
            name=name,
            price=price,
            pre_close=pre_close,
            open=open_price,
            high=high,
            low=low,
            change_pct=change_pct,
            volume=volume,
            amount=amount,
            turnover_rate=turnover_rate,
            volume_ratio=volume_ratio,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            ma120=ma120,
            ma250=ma250,
            rsi14=rsi14,
            atr14=atr14,
            atr14_pct=atr14_pct,
            boll_upper=boll_upper,
            boll_middle=boll_middle,
            boll_lower=boll_lower,
            boll_position=boll_position_value,
            return_5d=return_5d,
            return_20d=return_20d,
            rps20=rps20,
            trend_aligned=trend_aligned,
            industry=industry,
            board=board,
            is_st=is_st,
            is_suspended=is_suspended,
            total_mv=total_mv,
            circ_mv=circ_mv,
        )

    @staticmethod
    def compute_buy_zone(
        *,
        trigger_reference: float,
        confirmation_close: float,
        atr14: float,
        tick_size: float = 0.01,
    ) -> dict:
        """附录 A.3 BUY_ZONE_V1 + NO_CHASE_V1"""
        import math

        no_chase_price = min(
            trigger_reference * 1.015,
            confirmation_close + 0.15 * atr14,
        )
        no_chase_price = math.floor(no_chase_price / tick_size) * tick_size

        buy_zone_lower_raw = max(
            trigger_reference,
            confirmation_close - 0.10 * atr14,
        )
        buy_zone_lower = math.ceil(buy_zone_lower_raw / tick_size) * tick_size

        buy_zone_upper_raw = min(
            no_chase_price,
            confirmation_close + min(0.15 * atr14, confirmation_close * 0.005),
        )
        buy_zone_upper = math.floor(buy_zone_upper_raw / tick_size) * tick_size

        return {
            "buy_zone_lower": buy_zone_lower,
            "buy_zone_upper": buy_zone_upper,
            "no_chase_price": no_chase_price,
            "trigger_reference": trigger_reference,
            "empty": buy_zone_lower > buy_zone_upper,
            "confirmation_above_no_chase": confirmation_close > no_chase_price,
        }

    @staticmethod
    def compute_sell_zones(
        *,
        buy_zone_upper: float,
        atr14: float,
        resistance_1: Optional[float] = None,
        resistance_2: Optional[float] = None,
        tick_size: float = 0.01,
    ) -> dict:
        """附录 A.4 SELL_ZONES_V1"""
        import math

        first_center = buy_zone_upper + 0.75 * atr14
        if resistance_1 is not None:
            first_center = min(resistance_1, first_center)

        first_lower = first_center - 0.05 * atr14
        first_upper = first_center + 0.05 * atr14
        first_lower = math.ceil(first_lower / tick_size) * tick_size
        first_upper = math.floor(first_upper / tick_size) * tick_size

        if first_lower <= buy_zone_upper:
            return {
                "first_zone_lower": first_lower,
                "first_zone_upper": first_upper,
                "first_zone_center": first_center,
                "second_zone": "not_available",
                "error": "sell_zone_inside_buy_zone",
            }

        second_center = buy_zone_upper + 1.25 * atr14
        if resistance_2 is not None:
            second_center = min(resistance_2, second_center)

        second_lower = second_center - 0.05 * atr14
        second_upper = second_center + 0.05 * atr14
        second_lower = math.ceil(second_lower / tick_size) * tick_size
        second_upper = math.floor(second_upper / tick_size) * tick_size

        if second_lower <= first_upper:
            return {
                "first_zone_lower": first_lower,
                "first_zone_upper": first_upper,
                "first_zone_center": first_center,
                "second_zone": "not_available",
            }

        return {
            "first_zone_lower": first_lower,
            "first_zone_upper": first_upper,
            "first_zone_center": first_center,
            "second_zone_lower": second_lower,
            "second_zone_upper": second_upper,
            "second_zone_center": second_center,
        }

    @staticmethod
    def compute_protection(
        *,
        buy_zone_lower: float,
        buy_zone_upper: float,
        atr14: float,
        structural_support: float,
        tick_size: float = 0.01,
    ) -> dict:
        """附录 A.4 PROTECTION_V1"""
        import math

        raw_protection = max(
            structural_support - 0.15 * atr14,
            buy_zone_upper - 0.80 * atr14,
        )
        protection_price = min(
            raw_protection,
            buy_zone_lower - 2 * tick_size,
        )
        protection_price = math.floor(protection_price / tick_size) * tick_size

        constructible = protection_price < buy_zone_lower
        return {
            "protection_price": protection_price,
            "constructible": constructible,
            "structural_support": structural_support,
            "reason": "" if constructible else "protection_not_constructible",
        }

    @staticmethod
    def compute_fees(
        *,
        buy_price: float,
        sell_price: float,
        quantity: int = 100,
        fee_policy_id: str = "default_v1",
    ) -> dict:
        """附录 A.4 手续费后经济性"""
        policy = ObjectiveDataProvider.FEE_POLICY

        buy_amount = buy_price * quantity
        sell_amount = sell_price * quantity

        buy_commission = max(
            buy_amount * policy["commission_buy_rate"],
            policy["commission_min_per_order_cny"],
        )
        sell_commission = max(
            sell_amount * policy["commission_sell_rate"],
            policy["commission_min_per_order_cny"],
        )
        stamp_tax = sell_amount * policy["stamp_tax_sell_rate"]
        buy_transfer = buy_amount * policy["transfer_fee_buy_rate"]
        sell_transfer = sell_amount * policy["transfer_fee_sell_rate"]

        total_cost = buy_commission + sell_commission + stamp_tax + buy_transfer + sell_transfer
        net_profit = (sell_price - buy_price) * quantity - total_cost
        net_loss = (buy_price - sell_price) * quantity - total_cost

        return {
            "buy_commission": buy_commission,
            "sell_commission": sell_commission,
            "stamp_tax": stamp_tax,
            "buy_transfer": buy_transfer,
            "sell_transfer": sell_transfer,
            "total_cost": total_cost,
            "net_profit": net_profit,
            "net_loss": net_loss,
            "fee_policy_id": fee_policy_id,
        }

    @staticmethod
    def compute_upper_shadow(
        adjusted_high: float,
        adjusted_low: float,
        adjusted_open: float,
        adjusted_close: float,
    ) -> dict:
        """附录 A.5 上影线计算"""
        if adjusted_high == adjusted_low:
            return {"upper_shadow_ratio": 0.0, "long_upper_shadow": False}

        upper_shadow_ratio = (
            adjusted_high - max(adjusted_open, adjusted_close)
        ) / (adjusted_high - adjusted_low)

        return {
            "upper_shadow_ratio": round(upper_shadow_ratio, 4),
            "long_upper_shadow": upper_shadow_ratio >= 0.35,
        }

    @staticmethod
    def compute_overnight_gap(
        overnight_gaps: list[float],
    ) -> dict:
        """附录 A.5 隔夜跳空"""
        if len(overnight_gaps) < 60:
            return {"overnight_gap_p90": None, "overnight_gap_warning": False, "status": "history_insufficient"}

        sorted_gaps = sorted(overnight_gaps)
        p90 = sorted_gaps[int(len(sorted_gaps) * 0.9)]
        return {
            "overnight_gap_p90": round(p90, 4),
            "overnight_gap_warning": p90 >= 0.04,
            "status": "ok",
        }

    @staticmethod
    def compute_industry_crowding(
        industry_advance_ratio: float,
        industry_equal_weight_return: float,
        industry_amount: float,
        industry_amount_median_20d: float,
    ) -> dict:
        """附录 A.5 行业拥挤"""
        crowding = (
            industry_advance_ratio >= 0.85
            and industry_equal_weight_return >= 0.04
            and industry_amount / max(industry_amount_median_20d, 1) >= 1.50
        )
        return {
            "industry_crowding": crowding,
            "advance_ratio": industry_advance_ratio,
            "equal_weight_return": industry_equal_weight_return,
            "amount_ratio": industry_amount / max(industry_amount_median_20d, 1),
        }
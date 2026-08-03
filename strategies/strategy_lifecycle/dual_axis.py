"""
双轴判断模块 — 机会质量 × 尾部风险 9 格矩阵
============================================
按附录 A.5/A.7 确定性公式执行，输出唯一晋级/否决结果。
纯函数，不依赖任何 Skill 或模型调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OpportunityQuality(Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    POOR = "poor"


class TailRiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DualAxisDecision(Enum):
    CAN_ENTER_CANDIDATE = "can_enter_candidate"
    CONDITIONAL_WATCH = "conditional_watch"
    REJECT = "reject"


@dataclass(frozen=True)
class DualAxisResult:
    """双轴判断结果"""
    opportunity_quality: OpportunityQuality
    tail_risk: TailRiskLevel
    decision: DualAxisDecision
    op_score: int
    op_detail: str
    tr_count: int
    tr_flags: list[str] = field(default_factory=list)
    reason_code: str = ""


class DualAxis:
    """双轴判断 — 机会质量 × 尾部风险"""

    @staticmethod
    def compute_opportunity(
        *,
        trend_aligned: bool,
        rsi14: float,
        volume_ratio: float,
        change_pct: float,
        rps20: float,
        return_5d: float,
        return_20d: float,
        industry_sync_pass: bool,
        channel_type: str,
    ) -> tuple[OpportunityQuality, int, str]:
        """计算机会质量评分和分类"""
        score = 0
        details = []

        # 趋势
        if trend_aligned:
            score += 30
            details.append("trend_aligned")
        else:
            details.append("trend_not_aligned")

        # RSI
        if 40 <= rsi14 <= 70:
            score += 20
            details.append(f"rsi_healthy({rsi14:.0f})")
        elif rsi14 < 30:
            details.append(f"rsi_oversold({rsi14:.0f})")
        elif rsi14 > 80:
            details.append(f"rsi_overbought({rsi14:.0f})")

        # 量比
        if volume_ratio > 1.5:
            score += 15
            details.append(f"volume_high({volume_ratio:.1f}x)")
        elif volume_ratio > 0.8:
            score += 5
            details.append(f"volume_normal({volume_ratio:.1f}x)")
        else:
            details.append(f"volume_low({volume_ratio:.1f}x)")

        # 涨幅
        if 2 <= change_pct <= 7:
            score += 15
            details.append(f"change_moderate({change_pct:+.1f}%)")
        elif 0 < change_pct < 2:
            score += 5
            details.append(f"change_slight({change_pct:+.1f}%)")
        elif change_pct > 7:
            details.append(f"change_excessive({change_pct:+.1f}%)")

        # RPS
        if rps20 >= 80:
            score += 10
            details.append(f"rps_strong({rps20:.0f})")
        elif rps20 >= 50:
            score += 5
            details.append(f"rps_medium({rps20:.0f})")

        # 动量
        if return_5d > 0:
            score += 5
            details.append("return_5d_positive")
        if return_20d > 0:
            score += 5
            details.append("return_20d_positive")

        # 行业同步降级
        if not industry_sync_pass:
            details.append("industry_sync_failed")
            if score >= 60:
                # 从 high 降为 medium
                score = max(score - 15, 55)

        # 分类
        if score >= 60:
            quality = OpportunityQuality.STRONG
        elif score >= 40:
            quality = OpportunityQuality.MODERATE
        elif score >= 20:
            quality = OpportunityQuality.WEAK
        else:
            quality = OpportunityQuality.POOR

        return quality, score, " | ".join(details)

    @staticmethod
    def compute_tail_risk(
        *,
        rsi14: float,
        change_pct: float,
        price: float,
        ma20: float,
        turnover_rate: float,
        atr14_pct: float,
        overnight_gap_p90: Optional[float] = None,
        upper_shadow_ratio: float = 0.0,
        long_upper_shadow: bool = False,
        repeated_upper_shadow: bool = False,
        industry_crowding: bool = False,
        limit_proximity: bool = False,
        atr14_pct_cross_p80: Optional[float] = None,
    ) -> tuple[TailRiskLevel, int, list[str]]:
        """计算尾部风险分级和标签"""
        count = 0
        flags = []

        # RSI 超买
        if rsi14 > 80:
            count += 1
            flags.append("rsi_overbought")

        # 涨幅过大
        if change_pct > 9:
            count += 1
            flags.append("change_excessive_9pct")

        # 跌破 MA20
        if price < ma20 and ma20 > 0:
            count += 1
            flags.append("below_ma20")

        # 换手率过高
        if turnover_rate > 20:
            count += 1
            flags.append("turnover_high")

        # 高 ATR
        high_atr = atr14_pct >= 0.04
        if atr14_pct_cross_p80 is not None and atr14_pct >= atr14_pct_cross_p80:
            high_atr = True
        if high_atr:
            count += 1
            flags.append("high_atr_warning")

        # 隔夜跳空
        if overnight_gap_p90 is not None and overnight_gap_p90 >= 0.04:
            count += 1
            flags.append("overnight_gap_warning")

        # 长上影线
        if long_upper_shadow:
            count += 1
            flags.append("long_upper_shadow")

        # 重复上影线（不只是风险标签，同时也是机会质量硬失败）
        if repeated_upper_shadow:
            count += 1
            flags.append("repeated_upper_shadow")

        # 行业拥挤
        if industry_crowding:
            count += 1
            flags.append("industry_crowding")

        # 涨跌停临近
        if limit_proximity:
            count += 1
            flags.append("limit_proximity")

        if count >= 3:
            level = TailRiskLevel.HIGH
        elif count >= 1:
            level = TailRiskLevel.MEDIUM
        else:
            level = TailRiskLevel.LOW

        return level, count, flags

    @staticmethod
    def decide(
        opportunity_quality: OpportunityQuality,
        tail_risk: TailRiskLevel,
        repeated_upper_shadow: bool = False,
        market_veto: bool = False,
        protection_constructible: bool = True,
        sell_zone_constructible: bool = True,
    ) -> DualAxisDecision:
        """双轴决策矩阵 — 附录 A.7"""
        # 重复上影线对趋势延续和强势回踩是硬失败
        if repeated_upper_shadow:
            return DualAxisDecision.REJECT

        # 市场否决
        if market_veto:
            return DualAxisDecision.REJECT

        # 保护位/卖区不可构造
        if not protection_constructible or not sell_zone_constructible:
            return DualAxisDecision.REJECT

        # 双轴矩阵
        if opportunity_quality in (OpportunityQuality.STRONG, OpportunityQuality.MODERATE):
            if tail_risk in (TailRiskLevel.LOW, TailRiskLevel.MEDIUM):
                return DualAxisDecision.CAN_ENTER_CANDIDATE
            elif tail_risk == TailRiskLevel.HIGH:
                return DualAxisDecision.CONDITIONAL_WATCH
        elif opportunity_quality == OpportunityQuality.WEAK:
            if tail_risk == TailRiskLevel.LOW:
                return DualAxisDecision.CONDITIONAL_WATCH
            else:
                return DualAxisDecision.REJECT
        else:
            return DualAxisDecision.REJECT

        return DualAxisDecision.REJECT  # fallback

    @staticmethod
    def evaluate(
        *,
        trend_aligned: bool,
        rsi14: float,
        volume_ratio: float,
        change_pct: float,
        rps20: float,
        return_5d: float,
        return_20d: float,
        industry_sync_pass: bool,
        channel_type: str,
        price: float,
        ma20: float,
        turnover_rate: float,
        atr14_pct: float,
        repeated_upper_shadow: bool = False,
        overnight_gap_p90: Optional[float] = None,
        long_upper_shadow: bool = False,
        industry_crowding: bool = False,
        limit_proximity: bool = False,
        atr14_pct_cross_p80: Optional[float] = None,
        market_veto: bool = False,
        protection_constructible: bool = True,
        sell_zone_constructible: bool = True,
    ) -> DualAxisResult:
        """一站式双轴评估"""
        quality, op_score, op_detail = DualAxis.compute_opportunity(
            trend_aligned=trend_aligned,
            rsi14=rsi14,
            volume_ratio=volume_ratio,
            change_pct=change_pct,
            rps20=rps20,
            return_5d=return_5d,
            return_20d=return_20d,
            industry_sync_pass=industry_sync_pass,
            channel_type=channel_type,
        )

        risk, tr_count, tr_flags = DualAxis.compute_tail_risk(
            rsi14=rsi14,
            change_pct=change_pct,
            price=price,
            ma20=ma20,
            turnover_rate=turnover_rate,
            atr14_pct=atr14_pct,
            overnight_gap_p90=overnight_gap_p90,
            long_upper_shadow=long_upper_shadow,
            repeated_upper_shadow=repeated_upper_shadow,
            industry_crowding=industry_crowding,
            limit_proximity=limit_proximity,
            atr14_pct_cross_p80=atr14_pct_cross_p80,
        )

        decision = DualAxis.decide(
            opportunity_quality=quality,
            tail_risk=risk,
            repeated_upper_shadow=repeated_upper_shadow,
            market_veto=market_veto,
            protection_constructible=protection_constructible,
            sell_zone_constructible=sell_zone_constructible,
        )

        reason_code = ""
        if repeated_upper_shadow:
            reason_code = "terminal_distribution_risk"
        elif market_veto:
            reason_code = "market_veto"
        elif not protection_constructible:
            reason_code = "protection_not_constructible"
        elif not sell_zone_constructible:
            reason_code = "sell_zone_inside_buy_zone"

        return DualAxisResult(
            opportunity_quality=quality,
            tail_risk=risk,
            decision=decision,
            op_score=op_score,
            op_detail=op_detail,
            tr_count=tr_count,
            tr_flags=tr_flags,
            reason_code=reason_code,
        )
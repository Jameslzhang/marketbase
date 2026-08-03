"""
通道识别模块 — 按附录 A.1 确定性公式识别 4 个交易通道
========================================================
纯函数，无副作用，不依赖任何 Skill 或模型调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ChannelType(Enum):
    """四条交易通道"""
    TREND_CONTINUATION = "trend_continuation"
    STRONG_PULLBACK_RECLAIM = "strong_pullback_reclaim"
    HIGH_MOMENTUM = "high_momentum"
    SECTOR_REVERSAL_CHALLENGER = "sector_reversal_challenger"


@dataclass(frozen=True)
class ChannelResult:
    """单个通道的识别结果"""
    channel: ChannelType
    passed: bool
    production_buyable: bool = True
    reason: str = ""
    evidence: dict = field(default_factory=dict)


class ChannelIdentifier:
    """通道识别器 — 按附录 A.1 公式执行"""

    @staticmethod
    def identify_all(
        *,
        adjusted_close: float,
        ma11: float,
        ma23: float,
        rsi14: float,
        rps20: float,
        momentum_delta_1: float,
        momentum_delta_3: float,
        boll_position: float,
        return_20d: float,
        atr14: float,
        atr14_pct: float,
        repeated_upper_shadow: bool,
        full_day_vwap: Optional[float] = None,
        afternoon_avwap: Optional[float] = None,
        pre_reclaim_low: Optional[float] = None,
        pullback_vwap_frozen: Optional[float] = None,
        tick_size: float = 0.01,
        high_momentum_mode: str = "research_only",
    ) -> list[ChannelResult]:
        """对所有通道执行识别，返回结果列表"""
        results = []
        # 趋势延续
        results.append(ChannelIdentifier._check_trend_continuation(
            adjusted_close=adjusted_close, ma11=ma11, ma23=ma23,
            rsi14=rsi14, rps20=rps20, momentum_delta_1=momentum_delta_1,
            momentum_delta_3=momentum_delta_3, boll_position=boll_position,
            repeated_upper_shadow=repeated_upper_shadow,
        ))
        # 强势回踩
        results.append(ChannelIdentifier._check_strong_pullback(
            adjusted_close=adjusted_close, ma23=ma23,
            return_20d=return_20d, repeated_upper_shadow=repeated_upper_shadow,
            pre_reclaim_low=pre_reclaim_low, atr14=atr14,
            pullback_vwap_frozen=pullback_vwap_frozen, tick_size=tick_size,
        ))
        # 高动量 — 仅影子
        results.append(ChannelIdentifier._check_high_momentum(
            mode=high_momentum_mode, atr14_pct=atr14_pct,
        ))
        # 板块反转 — 仅影子
        results.append(ChannelIdentifier._check_sector_reversal())
        return results

    @staticmethod
    def _check_trend_continuation(
        adjusted_close: float, ma11: float, ma23: float,
        rsi14: float, rps20: float, momentum_delta_1: float,
        momentum_delta_3: float, boll_position: float,
        repeated_upper_shadow: bool,
    ) -> ChannelResult:
        """附录 A.1: trend_continuation_background"""
        reasons = []
        evidence = {}

        if not (adjusted_close > ma11 and ma11 >= ma23):
            reasons.append("price_not_above_ma_sequence")
        evidence["close_above_ma11"] = adjusted_close > ma11
        evidence["ma11_above_ma23"] = ma11 >= ma23

        if rps20 < 70:
            reasons.append(f"rps20_below_70 ({rps20:.1f})")
        evidence["rps20"] = rps20

        if not (momentum_delta_1 > 0 and momentum_delta_3 > 0):
            reasons.append("momentum_not_improving")
        evidence["momentum_improving"] = momentum_delta_1 > 0 and momentum_delta_3 > 0

        overheated = rsi14 >= 78 or (rsi14 >= 75 and boll_position >= 0.95)
        if overheated:
            reasons.append("overheated")
        evidence["overheated"] = overheated

        if repeated_upper_shadow:
            reasons.append("terminal_distribution_risk")
            passed = False
        else:
            passed = len(reasons) == 0

        evidence["repeated_upper_shadow"] = repeated_upper_shadow

        return ChannelResult(
            channel=ChannelType.TREND_CONTINUATION,
            passed=passed,
            production_buyable=True,
            reason="; ".join(reasons) if reasons else "pass",
            evidence=evidence,
        )

    @staticmethod
    def _check_strong_pullback(
        adjusted_close: float, ma23: float, return_20d: float,
        repeated_upper_shadow: bool, pre_reclaim_low: Optional[float],
        atr14: float, pullback_vwap_frozen: Optional[float],
        tick_size: float,
    ) -> ChannelResult:
        """附录 A.1: strong_pullback_background"""
        reasons = []
        evidence = {}

        if not (adjusted_close > ma23):
            reasons.append("price_not_above_ma23")
        evidence["close_above_ma23"] = adjusted_close > ma23

        if return_20d < 0.08:
            reasons.append(f"return_20d_below_8pct ({return_20d:.2%})")
        evidence["return_20d"] = return_20d

        if repeated_upper_shadow:
            reasons.append("terminal_distribution_risk")
            passed = False
        else:
            passed = len(reasons) == 0

        evidence["repeated_upper_shadow"] = repeated_upper_shadow

        # 回踩边界检查
        pullback_boundary_pass = False
        if pre_reclaim_low is not None and pullback_vwap_frozen is not None:
            pullback_depth = pre_reclaim_low / pullback_vwap_frozen - 1
            pullback_boundary_pass = (
                pullback_depth >= -0.015
                and pre_reclaim_low >= ma23 - 0.10 * atr14
            )
            evidence["pullback_depth"] = pullback_depth
            evidence["pullback_boundary_pass"] = pullback_boundary_pass
        else:
            evidence["pullback_boundary_pass"] = None
            evidence["pullback_depth"] = None

        if passed and not pullback_boundary_pass:
            reasons.append("pullback_boundary_failed")
            passed = False

        return ChannelResult(
            channel=ChannelType.STRONG_PULLBACK_RECLAIM,
            passed=passed,
            production_buyable=True,
            reason="; ".join(reasons) if reasons else "pass",
            evidence=evidence,
        )

    @staticmethod
    def _check_high_momentum(
        mode: str = "research_only", atr14_pct: float = 0.0,
    ) -> ChannelResult:
        """附录 A.5: HIGH_MOMENTUM_V1 — 当前仅影子"""
        return ChannelResult(
            channel=ChannelType.HIGH_MOMENTUM,
            passed=False,
            production_buyable=False,
            reason="research_only; production_buyable=false",
            evidence={"mode": mode, "atr14_pct": atr14_pct},
        )

    @staticmethod
    def _check_sector_reversal() -> ChannelResult:
        """附录 A.2: SECTOR_FIRST_REVERSAL_V1 — 当前仅影子"""
        return ChannelResult(
            channel=ChannelType.SECTOR_REVERSAL_CHALLENGER,
            passed=False,
            production_buyable=False,
            reason="shadow_only; contract_not_verified",
            evidence={},
        )
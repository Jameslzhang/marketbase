"""
利润保护模块 — 附录 A.10 / §5.3
================================
管理 open_validation_end 之后尚未退出的仓位的利润保护逻辑。
纯函数，参数可配置，影子运行默认值按 §8.5。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ProfitProtectionApplicability(Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    DATA_INSUFFICIENT = "data_insufficient"


@dataclass(frozen=True)
class ProfitProtectionResult:
    """利润保护评估结果"""
    triggered: bool
    applicability: ProfitProtectionApplicability
    target_progress: Optional[float] = None
    runup_high_since_activation: Optional[float] = None
    activation_at: Optional[datetime] = None
    activation_type: Optional[str] = None
    weakness_evidence: list[str] = field(default_factory=list)
    reason_code: str = ""
    detail: dict = field(default_factory=dict)


class ProfitProtection:
    """利润保护 — 影子运行默认参数按 §8.5"""

    SHADOW_PARAMS = {
        "target_progress_activation": 0.80,
        "drawdown_from_local_high_atr": 0.50,
        "weakness_confirm_minutes": 2,
        "profit_activity_ratio_max": 0.65,
    }

    def __init__(
        self,
        target_progress_activation: float = 0.80,
        drawdown_from_local_high_atr: float = 0.50,
        weakness_confirm_minutes: int = 2,
        profit_activity_ratio_max: float = 0.65,
        shadow_mode: bool = True,
    ):
        self.target_progress_activation = target_progress_activation
        self.drawdown_from_local_high_atr = drawdown_from_local_high_atr
        self.weakness_confirm_minutes = weakness_confirm_minutes
        self.profit_activity_ratio_max = profit_activity_ratio_max
        self.shadow_mode = shadow_mode

    def check_applicability(
        self,
        first_zone_lower: float,
        entry_price: float,
        buy_zone_upper: float,
    ) -> ProfitProtectionApplicability:
        """检查利润保护是否适用"""
        if first_zone_lower <= 0 or entry_price <= 0:
            return ProfitProtectionApplicability.DATA_INSUFFICIENT
        if first_zone_lower <= entry_price:
            return ProfitProtectionApplicability.NOT_APPLICABLE
        if first_zone_lower <= buy_zone_upper:
            return ProfitProtectionApplicability.NOT_APPLICABLE
        return ProfitProtectionApplicability.APPLICABLE

    def compute_target_progress(
        self,
        latest_executable_price: float,
        entry_price: float,
        first_zone_lower: float,
    ) -> Optional[float]:
        """计算当前目标推进进度"""
        if first_zone_lower <= entry_price:
            return None
        denominator = first_zone_lower - entry_price
        if denominator <= 0:
            return None
        return (latest_executable_price - entry_price) / denominator

    def is_activated(
        self,
        target_progress: Optional[float],
    ) -> bool:
        """检查是否达到激活阈值"""
        if target_progress is None:
            return False
        return target_progress >= self.target_progress_activation

    def check_weakness_evidence(
        self,
        *,
        drawdown_from_high: float,
        atr14: float,
        close_below_opening_reference: bool,
        close_below_vwap: bool,
        profit_activity_ratio: Optional[float] = None,
        price_making_new_high: bool = False,
        weakness_confirm_count: int = 0,
    ) -> tuple[bool, list[str]]:
        """检查弱势证据 — 至少两项满足 weakness_confirm_minutes"""
        evidence = []

        if drawdown_from_high >= self.drawdown_from_local_high_atr * atr14:
            evidence.append("drawdown_from_local_high")

        if close_below_opening_reference:
            evidence.append("close_below_opening_reference")

        if close_below_vwap:
            evidence.append("close_below_vwap")

        if (
            profit_activity_ratio is not None
            and profit_activity_ratio <= self.profit_activity_ratio_max
            and not price_making_new_high
        ):
            evidence.append("activity_ratio_weak")

        # 需要至少两项
        triggered = len(evidence) >= 2 and weakness_confirm_count >= self.weakness_confirm_minutes
        return triggered, evidence

    def evaluate(
        self,
        *,
        latest_executable_price: float,
        entry_price: float,
        first_zone_lower: float,
        buy_zone_upper: float,
        atr14: float,
        runup_high_since_activation: float,
        close_below_opening_reference: bool,
        close_below_vwap: bool,
        price_making_new_high: bool = False,
        profit_activity_ratio: Optional[float] = None,
        weakness_confirm_count: int = 0,
        activation_at: Optional[datetime] = None,
        activation_type: Optional[str] = None,
    ) -> ProfitProtectionResult:
        """一站式利润保护评估"""
        applicability = self.check_applicability(
            first_zone_lower=first_zone_lower,
            entry_price=entry_price,
            buy_zone_upper=buy_zone_upper,
        )

        if applicability != ProfitProtectionApplicability.APPLICABLE:
            return ProfitProtectionResult(
                triggered=False,
                applicability=applicability,
                reason_code="profit_protection_not_applicable",
                detail={"first_zone_lower": first_zone_lower, "entry_price": entry_price},
            )

        target_progress = self.compute_target_progress(
            latest_executable_price=latest_executable_price,
            entry_price=entry_price,
            first_zone_lower=first_zone_lower,
        )

        if target_progress is None:
            return ProfitProtectionResult(
                triggered=False,
                applicability=ProfitProtectionApplicability.DATA_INSUFFICIENT,
                reason_code="target_progress_data_insufficient",
            )

        if not self.is_activated(target_progress):
            return ProfitProtectionResult(
                triggered=False,
                applicability=applicability,
                target_progress=target_progress,
                reason_code="below_activation_threshold",
                detail={"target_progress": target_progress},
            )

        drawdown = runup_high_since_activation - latest_executable_price
        triggered, evidence = self.check_weakness_evidence(
            drawdown_from_high=drawdown,
            atr14=atr14,
            close_below_opening_reference=close_below_opening_reference,
            close_below_vwap=close_below_vwap,
            profit_activity_ratio=profit_activity_ratio,
            price_making_new_high=price_making_new_high,
            weakness_confirm_count=weakness_confirm_count,
        )

        return ProfitProtectionResult(
            triggered=triggered,
            applicability=applicability,
            target_progress=target_progress,
            runup_high_since_activation=runup_high_since_activation,
            activation_at=activation_at,
            activation_type=activation_type,
            weakness_evidence=evidence,
            reason_code="profit_protection_triggered" if triggered else "weakness_evidence_insufficient",
            detail={
                "target_progress": target_progress,
                "drawdown": drawdown,
                "atr14": atr14,
                "drawdown_threshold": self.drawdown_from_local_high_atr * atr14,
                "weakness_confirm_count": weakness_confirm_count,
            },
        )
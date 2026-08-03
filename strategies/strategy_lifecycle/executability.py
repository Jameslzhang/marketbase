"""
EXECUTABILITY_V1 + EXIT_EVENT_PRIORITY_V1 — 可执行性与退出优先级
==================================================================
按附录 A.6/A.7 实现，纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MarketExecutability(Enum):
    """市场可执行性"""
    EXECUTABLE = "executable"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"
    DATA_INSUFFICIENT = "data_insufficient"


class ExitEventType(Enum):
    """退出事件类型"""
    FAILED_OPEN_EXIT = "failed_open_exit"
    PROFIT_PROTECTION = "profit_protection"
    STRUCTURAL_STOP_EXIT = "structural_stop_exit"
    TARGET_EXIT = "target_exit"
    TIME_EXIT = "time_exit"
    EXIT_UNAVAILABLE = "exit_unavailable"


@dataclass
class ExecutabilityResult:
    """可执行性检查结果"""
    executability: MarketExecutability
    proxy_price: Optional[float] = None
    proxy_quantity: int = 0
    reason: str = ""


class ExecutabilityChecker:
    """
    EXECUTABILITY_V1 — 市场理论可执行 vs 用户实际成交分账

    正常可成交窗口条件：
    1. 连续竞价时段 [09:30,11:30) 或 [13:00,14:57)
    2. 证券状态正常，非停牌/临时停牌/退市整理
    3. volume >= proxy_required_quantity, amount > 0
    4. 买入分钟不是一字涨停，卖出分钟不是一字跌停
    5. 一字板瞬时开板不构成窗口，需连续两根完成分钟
    """

    @staticmethod
    def check_buy(
        *,
        price: float,
        high: float,
        low: float,
        open_price: float,
        close_price: float,
        volume: float,
        limit_up_price: float,
        is_suspended: bool = False,
        is_st: bool = False,
        in_continuous_session: bool = True,
        quantity: int = 100,
    ) -> ExecutabilityResult:
        """检查买入可执行性"""
        if is_suspended:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="suspended",
            )
        if not in_continuous_session:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="not_in_continuous_session",
            )
        # 一字涨停
        if open_price == high == low == close_price == limit_up_price:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="one_side_limit_up",
            )
        if volume < quantity:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="insufficient_volume",
            )

        return ExecutabilityResult(
            executability=MarketExecutability.EXECUTABLE,
            proxy_price=price,
            proxy_quantity=quantity,
            reason="normal_executable",
        )

    @staticmethod
    def check_sell(
        *,
        price: float,
        high: float,
        low: float,
        open_price: float,
        close_price: float,
        volume: float,
        limit_down_price: float,
        is_suspended: bool = False,
        in_continuous_session: bool = True,
        quantity: int = 100,
    ) -> ExecutabilityResult:
        """检查卖出可执行性"""
        if is_suspended:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="suspended",
            )
        if not in_continuous_session:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="not_in_continuous_session",
            )
        # 一字跌停
        if open_price == high == low == close_price == limit_down_price:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="one_side_limit_down",
            )
        if volume < quantity:
            return ExecutabilityResult(
                executability=MarketExecutability.UNAVAILABLE,
                reason="insufficient_volume",
            )

        return ExecutabilityResult(
            executability=MarketExecutability.EXECUTABLE,
            proxy_price=price,
            proxy_quantity=quantity,
            reason="normal_executable",
        )


class ExitEventPriority:
    """
    EXIT_EVENT_PRIORITY_V1 — 退出事件优先级

    规则（附录 A.7）：
    1. 开盘验证期内：failed_open_exit 优先于 profit_protection
    2. 开盘直接越过保护位 → structural_stop_exit（第一正常可成交窗口）
    3. 开盘达到第二卖区 → 全部退出（target_exit）
    4. 开盘达到第一卖区 → 一手仓位全部退出
    5. 无法成交 → exit_unavailable
    6. 接近目标后 profit_protection 和 failed_open_exit 互斥：
       开盘阶段由 failed_open_exit 管理，open_validation_end 之后由 profit_protection 管理
    """

    # 优先级：数字越小越优先
    PRIORITY = {
        ExitEventType.STRUCTURAL_STOP_EXIT: 1,   # 保护位失守最高优先
        ExitEventType.TARGET_EXIT: 2,             # 目标达到
        ExitEventType.FAILED_OPEN_EXIT: 3,         # 开盘失败
        ExitEventType.PROFIT_PROTECTION: 4,         # 利润保护
        ExitEventType.TIME_EXIT: 5,                 # 14:30 时间退出
    }

    @staticmethod
    def resolve(
        events: list[ExitEventType],
        in_open_validation: bool = False,
    ) -> Optional[ExitEventType]:
        """
        解析退出事件优先级

        开盘验证期内：
        - failed_open_exit 和 profit_protection 同时满足 → failed_open_exit
        - 保护位越过 → structural_stop_exit

        开盘验证后：
        - profit_protection 可触发
        """
        if not events:
            return None

        # 过滤：开盘验证期内 profit_protection 不参与
        if in_open_validation:
            events = [
                e for e in events
                if e != ExitEventType.PROFIT_PROTECTION
            ]

        if not events:
            return None

        # 按优先级排序
        return min(events, key=lambda e: ExitEventPriority.PRIORITY.get(e, 99))

    @staticmethod
    def is_open_validation(
        current_time: str,
        open_validation_end: str = "09:45",
    ) -> bool:
        """判断是否在开盘验证期内"""
        return current_time <= open_validation_end


@dataclass
class EarlyTargetReversalResult:
    """early_target_reversal 检测结果"""
    triggered: bool
    activation_type: Optional[str] = None  # "gap_arrival" or "intraday_runup"
    activation_minute: Optional[str] = None
    below_opening_reference: bool = False
    below_vwap: bool = False
    reason: str = ""


class EarlyTargetReversal:
    """
    early_target_reversal — 开盘冲高回落检测（§5.3 / §8.3）

    规则：
    1. 开盘后第 2 至第 10 分钟到达目标激活价
    2. 连续两根跌破开盘参考价与 VWAP
    3. 进入 failed_open_exit，不被 18 分钟活动度基线阻塞
    4. 开盘即位于激活价上方 → gap_arrival
    5. 从下方盘中首次穿越 → intraday_runup
    """

    @staticmethod
    def detect(
        *,
        opening_reference_price: float,
        entry_price: float,
        first_zone_lower: float,
        full_day_vwap: float,
        minute_prices: list[float],  # 最近分钟收盘价序列
        minute_vwaps: list[float],   # 对应分钟的 VWAP
        activation_progress: float = 0.80,
        confirm_minutes: int = 2,
    ) -> EarlyTargetReversalResult:
        """
        检测 early_target_reversal

        minute_prices: 按时间顺序的分钟收盘价（从开盘开始）
        minute_vwaps: 对应分钟的实时 VWAP
        """
        if not minute_prices or len(minute_prices) < 2:
            return EarlyTargetReversalResult(
                triggered=False, reason="insufficient_minute_data"
            )

        # 计算目标推进进度
        target_denominator = first_zone_lower - entry_price
        if target_denominator <= 0:
            return EarlyTargetReversalResult(
                triggered=False, reason="target_progress_denominator_invalid"
            )

        # 检测激活
        activation_type = None
        activation_minute = None
        activation_idx = -1

        for i, p in enumerate(minute_prices):
            progress = (p - entry_price) / target_denominator
            if progress >= activation_progress:
                if i == 0:
                    activation_type = "gap_arrival"
                else:
                    activation_type = "intraday_runup"
                activation_minute = str(i)
                activation_idx = i
                break

        if activation_idx < 0:
            return EarlyTargetReversalResult(
                triggered=False, reason="not_activated"
            )

        # 检查激活后是否有连续 confirm_minutes 根跌破开盘价和 VWAP
        if activation_idx + confirm_minutes >= len(minute_prices):
            return EarlyTargetReversalResult(
                triggered=False, reason="insufficient_post_activation_data"
            )

        below_open_count = 0
        below_vwap_count = 0
        for j in range(activation_idx + 1, min(activation_idx + 1 + confirm_minutes, len(minute_prices))):
            if minute_prices[j] < opening_reference_price:
                below_open_count += 1
            if j < len(minute_vwaps) and minute_prices[j] < minute_vwaps[j]:
                below_vwap_count += 1

        triggered = (
            below_open_count >= confirm_minutes
            and below_vwap_count >= confirm_minutes
        )

        return EarlyTargetReversalResult(
            triggered=triggered,
            activation_type=activation_type,
            activation_minute=activation_minute,
            below_opening_reference=below_open_count >= confirm_minutes,
            below_vwap=below_vwap_count >= confirm_minutes,
            reason="early_target_reversal_detected" if triggered else "conditions_not_met",
        )
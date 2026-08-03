"""
持仓退出状态机 — 14 个退出状态及所有合法转换
============================================
按 §5 状态图和转换表实现，纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ExitState(Enum):
    """退出状态 — 14 个状态"""
    OPENED_T1_LOCKED = "opened_t1_locked"
    NEXT_OPEN_OBSERVE = "next_open_observe"
    INTRADAY_POSITION_MONITOR = "intraday_position_monitor"
    FAILED_OPEN_EXIT = "failed_open_exit"
    PROFIT_PROTECTION = "profit_protection"
    STRUCTURAL_STOP_EXIT = "structural_stop_exit"
    TARGET_EXIT = "target_exit"
    TIME_EXIT = "time_exit"
    EXIT_UNAVAILABLE = "exit_unavailable"
    FORCED_CARRY_UNAVAILABLE = "forced_carry_unavailable"
    SUBSEQUENT_SESSION_EXIT_WATCH = "subsequent_session_exit_watch"
    CLOSED = "closed"
    USER_DEVIATION_HOLD = "user_deviation_hold"
    EXECUTION_UNKNOWN = "execution_unknown"


@dataclass(frozen=True)
class ExitTransition:
    """退出状态转换记录"""
    from_state: ExitState
    to_state: ExitState
    decision_id: str
    timestamp: datetime
    reason_code: str
    evidence: dict = field(default_factory=dict)


class IllegalExitTransition(Exception):
    """非法退出状态转换"""
    pass


LEGAL_EXIT_TRANSITIONS: dict[ExitState, set[ExitState]] = {
    ExitState.OPENED_T1_LOCKED: {
        ExitState.NEXT_OPEN_OBSERVE,
    },
    ExitState.NEXT_OPEN_OBSERVE: {
        ExitState.INTRADAY_POSITION_MONITOR,
        ExitState.FAILED_OPEN_EXIT,
        ExitState.TARGET_EXIT,
        ExitState.STRUCTURAL_STOP_EXIT,
        ExitState.EXIT_UNAVAILABLE,
    },
    ExitState.INTRADAY_POSITION_MONITOR: {
        ExitState.FAILED_OPEN_EXIT,
        ExitState.PROFIT_PROTECTION,
        ExitState.STRUCTURAL_STOP_EXIT,
        ExitState.TARGET_EXIT,
        ExitState.TIME_EXIT,
        ExitState.EXIT_UNAVAILABLE,
    },
    ExitState.FAILED_OPEN_EXIT: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.PROFIT_PROTECTION: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.STRUCTURAL_STOP_EXIT: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.TARGET_EXIT: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.TIME_EXIT: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.EXIT_UNAVAILABLE: {
        ExitState.FORCED_CARRY_UNAVAILABLE,
        ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
    },
    ExitState.FORCED_CARRY_UNAVAILABLE: {
        ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
    },
    ExitState.SUBSEQUENT_SESSION_EXIT_WATCH: {
        ExitState.CLOSED,
        ExitState.EXIT_UNAVAILABLE,
        ExitState.USER_DEVIATION_HOLD,
        ExitState.EXECUTION_UNKNOWN,
    },
    ExitState.USER_DEVIATION_HOLD: {
        ExitState.CLOSED,
        ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
    },
    ExitState.EXECUTION_UNKNOWN: {
        ExitState.CLOSED,
        ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
    },
    ExitState.CLOSED: set(),
}


class ExitStateMachine:
    """持仓退出状态机"""

    def __init__(self, code: str, strategy_profile_id: str, strategy_version: str):
        self.code = code
        self.strategy_profile_id = strategy_profile_id
        self.strategy_version = strategy_version
        self._state: ExitState = ExitState.OPENED_T1_LOCKED
        self._history: list[ExitTransition] = []
        self._entry_price: float = 0.0
        self._quantity: int = 0

    @property
    def state(self) -> ExitState:
        return self._state

    @property
    def history(self) -> list[ExitTransition]:
        return list(self._history)

    @property
    def entry_price(self) -> float:
        return self._entry_price

    def set_entry(self, price: float, quantity: int) -> None:
        """设置入场信息"""
        self._entry_price = price
        self._quantity = quantity

    def transition(
        self,
        to_state: ExitState,
        decision_id: str,
        reason_code: str,
        evidence: Optional[dict] = None,
    ) -> ExitTransition:
        if to_state not in LEGAL_EXIT_TRANSITIONS.get(self._state, set()):
            raise IllegalExitTransition(
                f"illegal_exit_transition: {self._state.value} -> {to_state.value} "
                f"for {self.code} ({self.strategy_profile_id})"
            )
        transition = ExitTransition(
            from_state=self._state,
            to_state=to_state,
            decision_id=decision_id,
            timestamp=datetime.utcnow(),
            reason_code=reason_code,
            evidence=evidence or {},
        )
        self._state = to_state
        self._history.append(transition)
        return transition

    def to_next_open_observe(self, decision_id: str, evidence: dict) -> ExitTransition:
        """T+1 锁定 → 次日开盘观察"""
        return self.transition(
            ExitState.NEXT_OPEN_OBSERVE, decision_id, "t1_lock_expired", evidence
        )

    def to_intraday_monitor(self, decision_id: str, evidence: dict) -> ExitTransition:
        """开盘验证结束 → 盘中监控"""
        return self.transition(
            ExitState.INTRADAY_POSITION_MONITOR, decision_id, "open_validation_end", evidence
        )

    def to_failed_open_exit(self, decision_id: str, evidence: dict) -> ExitTransition:
        """开盘失败退出"""
        return self.transition(
            ExitState.FAILED_OPEN_EXIT, decision_id, "failed_open_structure", evidence
        )

    def to_profit_protection(self, decision_id: str, evidence: dict) -> ExitTransition:
        """利润保护触发"""
        return self.transition(
            ExitState.PROFIT_PROTECTION, decision_id, "profit_protection_triggered", evidence
        )

    def to_structural_stop(self, decision_id: str, evidence: dict) -> ExitTransition:
        """结构止损"""
        return self.transition(
            ExitState.STRUCTURAL_STOP_EXIT, decision_id, "protection_breached", evidence
        )

    def to_target_exit(self, decision_id: str, evidence: dict) -> ExitTransition:
        """目标退出"""
        return self.transition(
            ExitState.TARGET_EXIT, decision_id, "target_reached", evidence
        )

    def to_time_exit(self, decision_id: str, evidence: dict) -> ExitTransition:
        """14:30 时间退出"""
        return self.transition(
            ExitState.TIME_EXIT, decision_id, "time_exit_1430", evidence
        )

    def to_exit_unavailable(self, decision_id: str, evidence: dict) -> ExitTransition:
        """无法退出"""
        return self.transition(
            ExitState.EXIT_UNAVAILABLE, decision_id, "exit_unavailable", evidence
        )

    def to_closed(self, decision_id: str, evidence: dict) -> ExitTransition:
        """已关闭"""
        return self.transition(
            ExitState.CLOSED, decision_id, "closed", evidence
        )

    def to_user_deviation_hold(self, decision_id: str, evidence: dict) -> ExitTransition:
        """用户拒绝退出"""
        return self.transition(
            ExitState.USER_DEVIATION_HOLD, decision_id, "user_rejected_exit", evidence
        )

    def to_execution_unknown(self, decision_id: str, evidence: dict) -> ExitTransition:
        """执行未知"""
        return self.transition(
            ExitState.EXECUTION_UNKNOWN, decision_id, "execution_unknown", evidence
        )

    def to_subsequent_session_watch(self, decision_id: str, evidence: dict) -> ExitTransition:
        """后续交易日退出监控"""
        return self.transition(
            ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
            decision_id, "subsequent_session", evidence,
        )

    def to_forced_carry(self, decision_id: str, evidence: dict) -> ExitTransition:
        """强制持有（无法退出）"""
        return self.transition(
            ExitState.FORCED_CARRY_UNAVAILABLE,
            decision_id, "forced_carry", evidence,
        )

    def is_active_position(self) -> bool:
        """是否活跃持仓"""
        return self._state not in (
            ExitState.CLOSED,
            ExitState.EXECUTION_UNKNOWN,
        )

    def needs_exit_intent(self) -> bool:
        """是否需要生成退出意图"""
        return self._state in (
            ExitState.NEXT_OPEN_OBSERVE,
            ExitState.INTRADAY_POSITION_MONITOR,
            ExitState.SUBSEQUENT_SESSION_EXIT_WATCH,
        )
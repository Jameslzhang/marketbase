"""
入场状态机 — 18 个入场状态及所有合法转换
==========================================
按 §4 状态图和转换表实现，纯函数，不依赖任何 Skill 或模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class EntryState(Enum):
    """入场状态 — 18 个状态"""
    UNASSESSED = "unassessed"
    DATA_INSUFFICIENT = "data_insufficient"
    REJECTED = "rejected"
    DEEP_WATCH = "deep_watch"
    CONDITIONAL_WATCH = "conditional_watch"
    CONFIRMED_CANDIDATE = "confirmed_candidate"
    PLAN_PUBLISHED = "plan_published"
    ENTRY_ACTIVE = "entry_active"
    CANCELED = "canceled"
    EXPIRED = "expired"
    USER_REPORTED_EXECUTED = "user_reported_executed"
    USER_REPORTED_NOT_EXECUTED = "user_reported_not_executed"
    EXECUTION_UNKNOWN = "execution_unknown"
    REJECTED_FOR_DAY = "rejected_for_day"


@dataclass(frozen=True)
class EntryTransition:
    """状态转换记录"""
    from_state: EntryState
    to_state: EntryState
    decision_id: str
    timestamp: datetime
    reason_code: str
    evidence: dict = field(default_factory=dict)


class IllegalStateTransition(Exception):
    """非法状态转换"""
    pass


# 合法转换表
LEGAL_TRANSITIONS: dict[EntryState, set[EntryState]] = {
    EntryState.UNASSESSED: {
        EntryState.DEEP_WATCH,
        EntryState.DATA_INSUFFICIENT,
        EntryState.REJECTED,
    },
    EntryState.DATA_INSUFFICIENT: {
        EntryState.DEEP_WATCH,
        EntryState.REJECTED,
        EntryState.REJECTED_FOR_DAY,
    },
    EntryState.REJECTED: {
        EntryState.DEEP_WATCH,
        EntryState.REJECTED_FOR_DAY,
    },
    EntryState.DEEP_WATCH: {
        EntryState.CONDITIONAL_WATCH,
        EntryState.DATA_INSUFFICIENT,
        EntryState.REJECTED,
        EntryState.REJECTED_FOR_DAY,
    },
    EntryState.CONDITIONAL_WATCH: {
        EntryState.CONFIRMED_CANDIDATE,
        EntryState.DATA_INSUFFICIENT,
        EntryState.CANCELED,
        EntryState.REJECTED_FOR_DAY,
    },
    EntryState.CONFIRMED_CANDIDATE: {
        EntryState.PLAN_PUBLISHED,
        EntryState.DATA_INSUFFICIENT,
        EntryState.CANCELED,
    },
    EntryState.PLAN_PUBLISHED: {
        EntryState.ENTRY_ACTIVE,
        EntryState.CANCELED,
        EntryState.EXPIRED,
    },
    EntryState.ENTRY_ACTIVE: {
        EntryState.CANCELED,
        EntryState.EXPIRED,
        EntryState.USER_REPORTED_EXECUTED,
        EntryState.USER_REPORTED_NOT_EXECUTED,
    },
    EntryState.CANCELED: {
        EntryState.USER_REPORTED_NOT_EXECUTED,
        EntryState.EXECUTION_UNKNOWN,
    },
    EntryState.EXPIRED: {
        EntryState.USER_REPORTED_NOT_EXECUTED,
        EntryState.EXECUTION_UNKNOWN,
    },
    EntryState.USER_REPORTED_EXECUTED: set(),
    EntryState.USER_REPORTED_NOT_EXECUTED: set(),
    EntryState.EXECUTION_UNKNOWN: set(),
    EntryState.REJECTED_FOR_DAY: set(),
}


class EntryStateMachine:
    """入场状态机 — 管理单只股票的入场状态转换"""

    def __init__(self, code: str, strategy_profile_id: str, strategy_version: str):
        self.code = code
        self.strategy_profile_id = strategy_profile_id
        self.strategy_version = strategy_version
        self._state: EntryState = EntryState.UNASSESSED
        self._history: list[EntryTransition] = []

    @property
    def state(self) -> EntryState:
        return self._state

    @property
    def history(self) -> list[EntryTransition]:
        return list(self._history)

    def transition(
        self,
        to_state: EntryState,
        decision_id: str,
        reason_code: str,
        evidence: Optional[dict] = None,
    ) -> EntryTransition:
        """执行状态转换，非法转换抛出异常"""
        if to_state not in LEGAL_TRANSITIONS.get(self._state, set()):
            raise IllegalStateTransition(
                f"illegal_state_transition: {self._state.value} -> {to_state.value} "
                f"for {self.code} ({self.strategy_profile_id})"
            )

        transition = EntryTransition(
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

    def to_unassessed_to_deep_watch(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """价格、日线、行业映射和可成交状态数据完整；至少一个通道背景初筛通过"""
        return self.transition(
            EntryState.DEEP_WATCH, decision_id, "data_complete_channel_pass", evidence
        )

    def to_unassessed_to_data_insufficient(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """任一主通道必要字段缺失、来源冲突或超时"""
        return self.transition(
            EntryState.DATA_INSUFFICIENT, decision_id, "channel_data_insufficient", evidence
        )

    def to_unassessed_to_rejected(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """所有通道背景硬条件均失败，或证券不可参与"""
        return self.transition(
            EntryState.REJECTED, decision_id, "all_channels_hard_fail", evidence
        )

    def to_deep_watch_to_conditional_watch(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """主通道冻结；结构支撑、观察触发和失效线可构造；机会质量至少 medium；尾部风险不是 high"""
        return self.transition(
            EntryState.CONDITIONAL_WATCH, decision_id, "channel_frozen_quality_ok", evidence
        )

    def to_conditional_watch_to_confirmed_candidate(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """最近3根已完成分钟唯一、连续且新鲜；确认窗口通过"""
        return self.transition(
            EntryState.CONFIRMED_CANDIDATE, decision_id, "minute_confirmation_pass", evidence
        )

    def to_confirmed_candidate_to_plan_published(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """买区、禁追价、保护位、卖区和手续费后经济性全部通过"""
        return self.transition(
            EntryState.PLAN_PUBLISHED, decision_id, "plan_construction_pass", evidence
        )

    def to_plan_published_to_entry_active(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """发布后复核通过，价格仍在买区，3根确认分钟仍有效"""
        return self.transition(
            EntryState.ENTRY_ACTIVE, decision_id, "entry_recheck_pass", evidence
        )

    def to_entry_active_to_user_reported_executed(
        self, decision_id: str, evidence: dict
    ) -> EntryTransition:
        """收到用户明确成交报告"""
        return self.transition(
            EntryState.USER_REPORTED_EXECUTED, decision_id, "user_reported_executed", evidence
        )

    def to_cancel(self, decision_id: str, reason_code: str, evidence: dict) -> EntryTransition:
        """任意状态取消"""
        return self.transition(
            EntryState.CANCELED, decision_id, reason_code, evidence
        )

    def to_data_insufficient(
        self, decision_id: str, reason_code: str, evidence: dict
    ) -> EntryTransition:
        """数据不足"""
        return self.transition(
            EntryState.DATA_INSUFFICIENT, decision_id, reason_code, evidence
        )

    def to_rejected(self, decision_id: str, reason_code: str, evidence: dict) -> EntryTransition:
        """拒绝"""
        return self.transition(
            EntryState.REJECTED, decision_id, reason_code, evidence
        )

    def to_expired(self, decision_id: str, evidence: dict) -> EntryTransition:
        """计划过期"""
        return self.transition(
            EntryState.EXPIRED, decision_id, "plan_expired", evidence
        )

    def can_enter_candidate_pool(self) -> bool:
        """是否可进入候选池"""
        return self._state in (
            EntryState.DEEP_WATCH,
            EntryState.CONDITIONAL_WATCH,
            EntryState.CONFIRMED_CANDIDATE,
        )

    def is_buyable(self) -> bool:
        """是否可买入"""
        return self._state == EntryState.ENTRY_ACTIVE
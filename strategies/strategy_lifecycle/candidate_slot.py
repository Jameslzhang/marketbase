"""
CANDIDATE_SLOT_V1 — 每天 1 个正式候选名额的释放与重分配
==========================================================
按 §4 实现，每个 strategy_profile_id + trade_date 独立分账。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class SlotStatus(Enum):
    FREE = "free"
    RESERVED = "reserved"       # confirmed_candidate 晋级时预留
    OCCUPIED = "occupied"       # plan_published 时正式占用
    RELEASED = "released"       # 释放（可重新分配）
    EMPTY_AFTER_CUTOFF = "empty_after_cutoff"


@dataclass
class SlotRecord:
    """候选名额记录"""
    strategy_profile_id: str
    trade_date: date
    code: str
    decision_id: str
    status: SlotStatus
    reserved_at: Optional[str] = None
    occupied_at: Optional[str] = None
    released_at: Optional[str] = None
    release_reason: Optional[str] = None


class CandidateSlotManager:
    """
    CANDIDATE_SLOT_V1 — 每天 1 个正式候选名额

    规则：
    1. confirmed_candidate 首次晋级时预留，plan_published 时正式占用
    2. confirmed_candidate → data_insufficient/canceled 且未发布计划时释放
    3. 替补按 机会质量高优先 → 尾部风险低优先 → 手续费后收益风险比高优先 → 股票代码升序
    4. plan_published/entry_active → canceled 后，只有 user_reported_not_executed
       或可验证未成交才释放；execution_unknown 不释放
    5. 释放后生成新 decision_id 和完整新鲜证据
    6. 到达截止时间后不重分配
    """

    def __init__(self, strategy_profile_id: str, trade_date: date):
        self.strategy_profile_id = strategy_profile_id
        self.trade_date = trade_date
        self._slot: SlotStatus = SlotStatus.FREE
        self._current: Optional[SlotRecord] = None
        self._history: list[SlotRecord] = []
        self._cutoff_reached: bool = False

    @property
    def status(self) -> SlotStatus:
        if self._cutoff_reached and self._slot != SlotStatus.OCCUPIED:
            return SlotStatus.EMPTY_AFTER_CUTOFF
        return self._slot

    @property
    def current_code(self) -> Optional[str]:
        if self._current:
            return self._current.code
        return None

    @property
    def is_available(self) -> bool:
        """名额是否可用（可预留）"""
        return self._slot in (SlotStatus.FREE, SlotStatus.RELEASED) and not self._cutoff_reached

    def reserve(
        self, code: str, decision_id: str, timestamp: str
    ) -> bool:
        """confirmed_candidate 晋级时预留名额"""
        if not self.is_available:
            return False

        self._slot = SlotStatus.RESERVED
        record = SlotRecord(
            strategy_profile_id=self.strategy_profile_id,
            trade_date=self.trade_date,
            code=code,
            decision_id=decision_id,
            status=SlotStatus.RESERVED,
            reserved_at=timestamp,
        )
        self._current = record
        self._history.append(record)
        return True

    def occupy(self, decision_id: str, timestamp: str) -> bool:
        """plan_published 时正式占用"""
        if self._slot != SlotStatus.RESERVED:
            return False
        if self._current and self._current.decision_id != decision_id:
            return False

        self._slot = SlotStatus.OCCUPIED
        if self._current:
            self._current.status = SlotStatus.OCCUPIED
            self._current.occupied_at = timestamp
        return True

    def release(
        self, reason: str, timestamp: str, can_reassign: bool = True
    ) -> bool:
        """
        释放名额
        can_reassign=True: 可重新分配（confirmed_candidate 取消且未发布）
        can_reassign=False: 不可重新分配（execution_unknown）
        """
        if self._slot == SlotStatus.OCCUPIED and not can_reassign:
            # 已占用且 execution_unknown → 不释放
            return False

        if self._slot in (SlotStatus.RESERVED, SlotStatus.OCCUPIED):
            self._slot = SlotStatus.RELEASED if can_reassign else SlotStatus.OCCUPIED
            if self._current:
                self._current.released_at = timestamp
                self._current.release_reason = reason
            return True
        return False

    def set_cutoff(self) -> None:
        """到达截止时间"""
        self._cutoff_reached = True
        if self._slot not in (SlotStatus.OCCUPIED,):
            self._slot = SlotStatus.EMPTY_AFTER_CUTOFF

    @staticmethod
    def rank_substitutes(candidates: list[dict]) -> list[dict]:
        """
        替补排序：机会质量高优先 → 尾部风险低优先 → 收益风险比高优先 → 代码升序
        每个 candidate 应包含:
          - code, decision_id
          - opportunity_quality (strong=3, moderate=2, weak=1, poor=0)
          - tail_risk (low=0, medium=1, high=2)
          - reward_risk_ratio
        """
        quality_map = {"strong": 3, "moderate": 2, "weak": 1, "poor": 0}
        risk_map = {"low": 0, "medium": 1, "high": 2}

        return sorted(
            candidates,
            key=lambda c: (
                -quality_map.get(c.get("opportunity_quality", "poor"), 0),
                risk_map.get(c.get("tail_risk", "high"), 2),
                -c.get("reward_risk_ratio", 0),
                c.get("code", "999999"),
            ),
        )

    def get_history(self) -> list[SlotRecord]:
        return list(self._history)
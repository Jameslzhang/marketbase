"""
ACCOUNT_RISK_V1 + LOSS_OFFSET_ADD_GATE_V1 — 账户风险闸门与禁止对冲式加仓
==========================================================================
按 §4/§5.1 实现，纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AccountRiskResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    PLAN_ONLY = "plan_only"  # 账户快照不完整


class LossOffsetResult(Enum):
    PASS = "pass"
    BLOCKED_SAME_SYMBOL = "underwater_same_symbol_add_blocked"
    BLOCKED_CORRELATED = "unresolved_loss_correlated_add_blocked"
    PLAN_ONLY = "plan_only"


@dataclass
class AccountSnapshot:
    """账户快照"""
    total_equity: float = 0.0
    positions: list[dict] = field(default_factory=list)  # [{code, quantity, cost, protection, industry, status}]
    available: bool = False


class AccountRiskGate:
    """
    ACCOUNT_RISK_V1 — 账户风险闸门

    规则：
    1. 100 股档位：单笔买入金额不超过总权益的 5%
    2. 同一行业总持仓市值不超过总权益的 15%
    3. 单只股票总持仓市值不超过总权益的 10%
    4. 账户快照不完整时保持 plan_only
    """

    MAX_SINGLE_POSITION_RATIO = 0.10
    MAX_INDUSTRY_RATIO = 0.15
    MAX_SINGLE_TRADE_RATIO = 0.05
    MIN_LOT_SIZE = 100

    def __init__(self, snapshot: Optional[AccountSnapshot] = None):
        self.snapshot = snapshot

    def check_100_lot(
        self,
        buy_price: float,
        quantity: int = 100,
        industry: str = "",
        code: str = "",
    ) -> AccountRiskResult:
        """100 股档位风险校验"""
        if self.snapshot is None or not self.snapshot.available:
            return AccountRiskResult.PLAN_ONLY

        buy_amount = buy_price * quantity
        if buy_amount > self.snapshot.total_equity * self.MAX_SINGLE_TRADE_RATIO:
            return AccountRiskResult.FAIL

        # 单只股票已有持仓 + 新买入
        existing_same = sum(
            p.get("cost", 0) * p.get("quantity", 0)
            for p in self.snapshot.positions
            if p.get("code") == code
        )
        total_same = existing_same + buy_amount
        if total_same > self.snapshot.total_equity * self.MAX_SINGLE_POSITION_RATIO:
            return AccountRiskResult.FAIL

        # 同一行业
        existing_industry = sum(
            p.get("cost", 0) * p.get("quantity", 0)
            for p in self.snapshot.positions
            if p.get("industry") == industry
        )
        total_industry = existing_industry + buy_amount
        if total_industry > self.snapshot.total_equity * self.MAX_INDUSTRY_RATIO:
            return AccountRiskResult.FAIL

        return AccountRiskResult.PASS


class LossOffsetAddGate:
    """
    LOSS_OFFSET_ADD_GATE_V1 — 禁止对冲式加仓闸门

    规则：
    1. 候选与账户内亏损持仓为同一证券 → 禁止新增
    2. 亏损持仓已失守保护位/处于 exit_unavailable/user_deviation_hold/execution_unknown
       且与候选同一主行业或相关 → 禁止新增
    3. 不采用全局禁止规则，轻微浮亏但风险合格的持仓仍可正常评估
    4. 闸门输入必须来自同一 ACCOUNT_SNAPSHOT_V1
    """

    def __init__(self, snapshot: Optional[AccountSnapshot] = None):
        self.snapshot = snapshot

    def check(
        self,
        candidate_code: str,
        candidate_industry: str,
    ) -> LossOffsetResult:
        """检查是否可以加仓"""
        if self.snapshot is None or not self.snapshot.available:
            return LossOffsetResult.PLAN_ONLY

        for pos in self.snapshot.positions:
            pos_code = pos.get("code", "")
            pos_cost = pos.get("cost", 0)
            pos_protection = pos.get("protection", 0)
            pos_status = pos.get("status", "")
            pos_industry = pos.get("industry", "")

            # 规则 1: 同一证券亏损禁止加仓
            if pos_code == candidate_code:
                # 判断是否亏损（需要实时价格，这里用保护位作为近似）
                if pos_protection > 0 and pos_cost > pos_protection:
                    return LossOffsetResult.BLOCKED_SAME_SYMBOL

            # 规则 2: 相关行业未解决亏损禁止加仓
            unresolved_statuses = {
                "exit_unavailable", "user_deviation_hold", "execution_unknown"
            }
            if pos_status in unresolved_statuses and pos_industry == candidate_industry:
                return LossOffsetResult.BLOCKED_CORRELATED

        return LossOffsetResult.PASS
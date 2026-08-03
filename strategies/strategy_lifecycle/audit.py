"""
审计对账模块 — §7 四类责任分账 + 决策ID追踪
==========================================
纯函数，不依赖任何 Skill 或模型调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AuditCategory(Enum):
    """四类审计责任"""
    SELECTION_FAILURE = "selection_failure"
    ENTRY_FAILURE = "entry_failure"
    EXIT_FAILURE = "exit_failure"
    EXECUTION_FAILURE = "execution_failure"


class ReconciliationState(Enum):
    """对账状态"""
    UNVERIFIED = "unverified"
    SCREENSHOT_VERIFIED = "screenshot_verified"
    BROKER_RECORD_VERIFIED = "broker_record_verified"
    CONFLICT_PENDING = "conflict_pending"
    CORRECTED = "corrected"


@dataclass
class DecisionRecord:
    """单条决策记录"""
    decision_id: str
    strategy_profile_id: str
    strategy_version: str
    lifecycle_contract_version: str
    schema_version: str
    execution_rule_version: str
    skill_version: str
    code: str
    timestamp: datetime
    action: str
    state_before: str
    state_after: str
    reason_code: str
    evidence: dict = field(default_factory=dict)
    provider_hash: Optional[str] = None
    formula_version: Optional[str] = None

    def validate_versions(self) -> list[str]:
        """验证六个版本字段完整性"""
        missing = []
        required = {
            "strategy_profile_id": self.strategy_profile_id,
            "strategy_version": self.strategy_version,
            "lifecycle_contract_version": self.lifecycle_contract_version,
            "schema_version": self.schema_version,
            "execution_rule_version": self.execution_rule_version,
            "skill_version": self.skill_version,
        }
        for field_name, value in required.items():
            if not value or value in ("legacy", ""):
                missing.append(field_name)
        return missing


@dataclass
class AuditResult:
    """审计结果"""
    category: AuditCategory
    decision_id: str
    code: str
    detail: str
    evidence: dict = field(default_factory=dict)


class AuditTrail:
    """审计追踪 — 按 decision_id 串联全链路"""

    def __init__(self):
        self._records: list[DecisionRecord] = []
        self._audit_results: list[AuditResult] = []

    def add_record(self, record: DecisionRecord) -> None:
        """添加决策记录"""
        self._records.append(record)

    def add_audit(self, result: AuditResult) -> None:
        """添加审计结果"""
        self._audit_results.append(result)

    def get_records_by_decision_id(self, decision_id: str) -> list[DecisionRecord]:
        """按 decision_id 查询"""
        return [r for r in self._records if r.decision_id == decision_id]

    def get_records_by_code(self, code: str) -> list[DecisionRecord]:
        """按股票代码查询"""
        return [r for r in self._records if r.code == code]

    def get_records_by_profile(self, profile_id: str) -> list[DecisionRecord]:
        """按策略配置查询"""
        return [r for r in self._records if r.strategy_profile_id == profile_id]

    def verify_full_chain(self, decision_id: str) -> bool:
        """验证完整链路：Provider快照 → 公式中间值 → 状态转换 → Skill输出 → 审计"""
        records = self.get_records_by_decision_id(decision_id)
        if not records:
            return False

        # 检查是否有版本字段缺失
        for record in records:
            missing = record.validate_versions()
            if missing:
                return False

        return True

    def classify_failure(
        self,
        decision_id: str,
        code: str,
        category: AuditCategory,
        detail: str,
        evidence: Optional[dict] = None,
    ) -> AuditResult:
        """分类失败"""
        result = AuditResult(
            category=category,
            decision_id=decision_id,
            code=code,
            detail=detail,
            evidence=evidence or {},
        )
        self._audit_results.append(result)
        return result

    @property
    def records(self) -> list[DecisionRecord]:
        return list(self._records)

    @property
    def audit_results(self) -> list[AuditResult]:
        return list(self._audit_results)

    def get_metrics(self) -> dict:
        """计算审计指标"""
        total = len(self._records)
        if total == 0:
            return {}

        categories = {}
        for r in self._audit_results:
            cat = r.category.value
            if cat not in categories:
                categories[cat] = 0
            categories[cat] += 1

        return {
            "total_decisions": total,
            "total_audits": len(self._audit_results),
            "by_category": categories,
        }
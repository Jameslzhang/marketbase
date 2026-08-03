"""
版本兼容与原子切换 — §8.2 实现
==============================
管理策略版本迁移、原子切换闸门、旧记录兼容读取和回退机制。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ActivationGateStatus(Enum):
    """原子切换闸门状态"""
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"


@dataclass
class VersionRecord:
    """版本记录"""
    strategy_profile_id: str
    strategy_version: str
    lifecycle_contract_version: str
    schema_version: str
    execution_rule_version: str
    skill_version: str
    effective_from: Optional[datetime] = None
    status: str = "draft"


@dataclass
class MigrationRecord:
    """迁移记录"""
    decision_id: str
    original_strategy_version: str
    migrated_strategy_version: str
    migration_time: datetime
    missing_fields: list[str]
    reason_codes: list[str]
    status: str = "migrated"


class VersionManager:
    """版本管理器 — 原子切换与兼容"""

    REQUIRED_VERSIONS = [
        "strategy_profile_id",
        "strategy_version",
        "lifecycle_contract_version",
        "schema_version",
        "execution_rule_version",
        "skill_version",
    ]

    def __init__(self):
        self._versions: dict[str, VersionRecord] = {}
        self._migrations: list[MigrationRecord] = []
        self._activation_gate: ActivationGateStatus = ActivationGateStatus.PENDING

    def register_version(self, record: VersionRecord) -> None:
        """注册版本"""
        self._versions[record.strategy_version] = record

    def validate_legacy_record(self, record: dict) -> MigrationRecord:
        """验证旧记录并生成迁移记录"""
        missing = []
        reason_codes = []

        for field in self.REQUIRED_VERSIONS:
            if field not in record or not record[field]:
                missing.append(field)
                reason_codes.append(f"missing_{field}")

        migration = MigrationRecord(
            decision_id=record.get("decision_id", "unknown"),
            original_strategy_version=record.get("strategy_version", "legacy"),
            migrated_strategy_version="2.0.0",
            migration_time=datetime.utcnow(),
            missing_fields=missing,
            reason_codes=reason_codes,
            status="legacy_compatible" if missing else "fully_compatible",
        )
        self._migrations.append(migration)
        return migration

    def validate_new_record(self, record: dict) -> list[str]:
        """验证新版记录 — 缺任一必填字段返回 schema_rejected"""
        missing = []
        for field in self.REQUIRED_VERSIONS:
            if not record.get(field):
                missing.append(field)
        return missing

    def check_activation_gate(
        self,
        *,
        structural_tests_pass: bool = True,
        dual_implementation_consistent: bool = True,
        regression_samples_pass: bool = True,
        schema_migration_pass: bool = True,
        shadow_acceptance_pass: bool = True,
        candidate_rate_acceptable: bool = True,
        empty_days_acceptable: bool = True,
    ) -> ActivationGateStatus:
        """
        VERSION_ACTIVATION_GATE_V1 — 原子切换闸门

        所有条件必须同时满足，否则不得设置 effective_from。
        """
        failures = []

        if not structural_tests_pass:
            failures.append("structural_tests")
        if not dual_implementation_consistent:
            failures.append("dual_implementation")
        if not regression_samples_pass:
            failures.append("regression_samples")
        if not schema_migration_pass:
            failures.append("schema_migration")
        if not shadow_acceptance_pass:
            failures.append("shadow_acceptance")
        if not candidate_rate_acceptable:
            failures.append("candidate_rate")
        if not empty_days_acceptable:
            failures.append("empty_days")

        if failures:
            self._activation_gate = ActivationGateStatus.FAILED
            self._activation_gate_failures = failures
        else:
            self._activation_gate = ActivationGateStatus.VERIFIED

        return self._activation_gate

    def activate(self, effective_from: datetime) -> bool:
        """激活版本 — 设置 effective_from"""
        if self._activation_gate != ActivationGateStatus.VERIFIED:
            return False
        self._activation_gate = ActivationGateStatus.ACTIVE
        self._effective_from = effective_from
        return True

    def rollback(self) -> bool:
        """回退到上一版本"""
        if self._activation_gate == ActivationGateStatus.ACTIVE:
            self._activation_gate = ActivationGateStatus.ROLLED_BACK
            return True
        return False

    @property
    def activation_gate(self) -> ActivationGateStatus:
        return self._activation_gate

    @property
    def effective_from(self) -> Optional[datetime]:
        return getattr(self, "_effective_from", None)

    @property
    def migrations(self) -> list[MigrationRecord]:
        return list(self._migrations)

    def is_active(self) -> bool:
        return self._activation_gate == ActivationGateStatus.ACTIVE

    def validate_decision_version_consistency(
        self, decisions: list[dict], decision_id: str
    ) -> bool:
        """同一 decision_id 从候选到审计必须使用同一 strategy_version"""
        versions = set()
        for d in decisions:
            if d.get("decision_id") == decision_id:
                versions.add(d.get("strategy_version", ""))

        return len(versions) <= 1 and "" not in versions


class SchemaValidator:
    """Schema 校验器"""

    V2_REQUIRED_FIELDS = {
        "decision_id": str,
        "strategy_profile_id": str,
        "strategy_version": str,
        "lifecycle_contract_version": str,
        "schema_version": str,
        "execution_rule_version": str,
        "skill_version": str,
        "code": str,
        "timestamp": (str, datetime),
        "action": str,
        "state_before": str,
        "state_after": str,
        "reason_code": str,
    }

    @staticmethod
    def validate_v2(record: dict) -> list[str]:
        """验证 V2 Schema 完整性"""
        errors = []
        for field, expected_type in SchemaValidator.V2_REQUIRED_FIELDS.items():
            if field not in record:
                errors.append(f"missing_field:{field}")
            elif not record[field]:
                errors.append(f"empty_field:{field}")
        return errors

    @staticmethod
    def validate_legacy(record: dict) -> list[str]:
        """验证旧记录兼容性"""
        warnings = []
        # 旧记录必须至少有 code 和 decision_id
        if "code" not in record:
            warnings.append("missing_code")
        if "decision_id" not in record:
            warnings.append("missing_decision_id")
        return warnings

    @staticmethod
    def is_legacy(record: dict) -> bool:
        """判断是否为旧记录"""
        if record.get("schema_version") in ("legacy", "", None):
            return True
        if record.get("strategy_version") in ("legacy", "", None):
            return True
        # 缺少2个以上版本字段视为旧记录
        missing = sum(
            1 for f in VersionManager.REQUIRED_VERSIONS
            if not record.get(f)
        )
        return missing >= 2
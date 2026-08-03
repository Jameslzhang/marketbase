"""
A股T+1全生命周期策略执行层
=============================

独立于 marketbase 包，运行确定性规则计算。
读取 Marketbase 冻结数据，输出策略产物。

架构分层（参见 V08031138_全市场T1策略规格.md）：
  Marketbase（客观数据） → Strategy Engine（规则计算） → Codex（解释建议）
"""

from .strategy_lifecycle import (
    ChannelIdentifier, ChannelType, ChannelResult,
    DualAxis, OpportunityQuality, TailRiskLevel, DualAxisResult,
    EntryState, EntryStateMachine, EntryTransition,
    ExitState, ExitStateMachine, ExitTransition,
    ProfitProtection, ProfitProtectionResult,
    AuditTrail, DecisionRecord, AuditResult, AuditCategory, ReconciliationState,
    VersionManager, VersionRecord, SchemaValidator, MigrationRecord, ActivationGateStatus,
    CandidateSlotManager, SlotRecord, SlotStatus,
    AccountRiskGate, LossOffsetAddGate, AccountSnapshot, AccountRiskResult, LossOffsetResult,
    ExecutabilityChecker, ExitEventPriority, ExitEventType,
    MarketExecutability, ExecutabilityResult,
    EarlyTargetReversal, EarlyTargetReversalResult,
    DecisionLogger, DebugLogManager,
)

from .t1_snapshot import build_t1_snapshot, build_t1_snapshot_v2
from .t1_analysis import run_analysis, run_analysis_v2

__all__ = [
    "ChannelIdentifier", "ChannelType", "ChannelResult",
    "DualAxis", "OpportunityQuality", "TailRiskLevel", "DualAxisResult",
    "EntryState", "EntryStateMachine", "EntryTransition",
    "ExitState", "ExitStateMachine", "ExitTransition",
    "ProfitProtection", "ProfitProtectionResult",
    "AuditTrail", "DecisionRecord", "AuditResult", "AuditCategory", "ReconciliationState",
    "VersionManager", "VersionRecord", "SchemaValidator", "MigrationRecord", "ActivationGateStatus",
    "CandidateSlotManager", "SlotRecord", "SlotStatus",
    "AccountRiskGate", "LossOffsetAddGate", "AccountSnapshot", "AccountRiskResult", "LossOffsetResult",
    "ExecutabilityChecker", "ExitEventPriority", "ExitEventType",
    "MarketExecutability", "ExecutabilityResult",
    "EarlyTargetReversal", "EarlyTargetReversalResult",
    "DecisionLogger", "DebugLogManager",
    "build_t1_snapshot", "build_t1_snapshot_v2",
    "run_analysis", "run_analysis_v2",
]
"""
A股T+1全生命周期策略执行层
=============================

本子包实现统一主规范 V1.6 中定义的确定性策略逻辑：
- 通道识别 (channel_identifier)
- 双轴判断 (dual_axis)
- 入场状态机 (entry_state_machine)
- 持仓退出状态机 (exit_state_machine)
- 利润保护 (profit_protection)
- 审计对账 (audit)
- 版本兼容与原子切换 (version_compat)
- 候选名额管理 (candidate_slot)
- 账户风险与禁止对冲 (account_risk)
- 可执行性与退出优先级 (executability)
- DEBUG 日志 (debug_logger)

所有模块均为纯函数（无副作用、无IO、无模型调用），
输出唯一由输入确定，可复现、可测试。
"""

from .channel_identifier import ChannelIdentifier, ChannelType, ChannelResult
from .dual_axis import DualAxis, OpportunityQuality, TailRiskLevel, DualAxisResult
from .entry_state_machine import EntryState, EntryStateMachine, EntryTransition
from .exit_state_machine import ExitState, ExitStateMachine, ExitTransition
from .profit_protection import ProfitProtection, ProfitProtectionResult
from .audit import AuditTrail, DecisionRecord, AuditResult, AuditCategory, ReconciliationState
from .version_compat import (
    VersionManager, VersionRecord, SchemaValidator,
    MigrationRecord, ActivationGateStatus,
)
from .candidate_slot import CandidateSlotManager, SlotRecord, SlotStatus
from .account_risk import AccountRiskGate, LossOffsetAddGate, AccountSnapshot, AccountRiskResult, LossOffsetResult
from .executability import (
    ExecutabilityChecker, ExitEventPriority, ExitEventType,
    MarketExecutability, ExecutabilityResult,
    EarlyTargetReversal, EarlyTargetReversalResult,
)
from .debug_logger import DecisionLogger, DebugLogManager

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
    "DecisionLogger", "DebugLogManager",
]
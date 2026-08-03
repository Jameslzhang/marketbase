"""
DEBUG 日志 — 按 decision_id 串联全链路
========================================
前 5 个交易日完整 DEBUG 日志，所有状态转换、公式计算中间值、
数据不足原因、Provider 响应哈希、版本字段、用户报告对账和
Skill 输出适配，必须可以从日志追溯到同一 decision_id。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


class DecisionLogger:
    """按 decision_id 串联全链路的 DEBUG 日志器"""

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        decision_id: str = "",
        strategy_profile_id: str = "",
        strategy_version: str = "",
    ):
        self.decision_id = decision_id
        self.strategy_profile_id = strategy_profile_id
        self.strategy_version = strategy_version
        self.log_dir = Path(log_dir) if log_dir else Path("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"strategy.{decision_id}")
        self.logger.setLevel(logging.DEBUG)

        if not self.logger.handlers:
            log_file = self.log_dir / f"strategy_{decision_id}.log"
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)
            self._handler = fh

    def close(self) -> None:
        """关闭日志处理器"""
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

    def _format(self, msg: str, **kwargs) -> str:
        """格式化日志消息，附加 decision_id 和版本字段"""
        extras = {
            "decision_id": self.decision_id,
            "profile": self.strategy_profile_id,
            "version": self.strategy_version,
        }
        extras.update(kwargs)
        extra_str = json.dumps(extras, ensure_ascii=False, default=str)
        return f"{msg} | {extra_str}"

    def log_provider_snapshot(self, evidence_id: str, provider_hash: str, formula_version: str) -> None:
        """Provider 快照"""
        self.logger.debug(
            self._format(
                "provider_snapshot",
                evidence_id=evidence_id,
                provider_hash=provider_hash,
                formula_version=formula_version,
            )
        )

    def log_formula_intermediate(self, formula: str, inputs: dict, output: dict) -> None:
        """公式计算中间值"""
        self.logger.debug(
            self._format(
                f"formula.{formula}",
                inputs=inputs,
                output=output,
            )
        )

    def log_state_transition(self, from_state: str, to_state: str, reason_code: str, evidence: dict) -> None:
        """状态转换"""
        self.logger.debug(
            self._format(
                "state_transition",
                from_state=from_state,
                to_state=to_state,
                reason_code=reason_code,
                evidence=evidence,
            )
        )

    def log_data_insufficient(self, reason: str, missing_fields: list[str]) -> None:
        """数据不足"""
        self.logger.warning(
            self._format(
                "data_insufficient",
                reason=reason,
                missing_fields=missing_fields,
            )
        )

    def log_skill_output(self, skill_name: str, output_summary: str) -> None:
        """Skill 输出适配"""
        self.logger.debug(
            self._format(
                f"skill.{skill_name}",
                output_summary=output_summary,
            )
        )

    def log_user_execution(self, execution_type: str, price: float, quantity: int, evidence_source: str) -> None:
        """用户执行报告"""
        self.logger.info(
            self._format(
                "user_execution",
                type=execution_type,
                price=price,
                quantity=quantity,
                evidence_source=evidence_source,
            )
        )

    def log_reconciliation(self, state: str, detail: str) -> None:
        """对账"""
        self.logger.info(
            self._format(
                "reconciliation",
                state=state,
                detail=detail,
            )
        )

    def log_error(self, error_type: str, detail: str, context: Optional[dict] = None) -> None:
        """错误"""
        self.logger.error(
            self._format(
                f"error.{error_type}",
                detail=detail,
                context=context or {},
            )
        )

    def log_audit(self, category: str, detail: str, metrics: Optional[dict] = None) -> None:
        """审计"""
        self.logger.info(
            self._format(
                "audit",
                category=category,
                detail=detail,
                metrics=metrics or {},
            )
        )


class DebugLogManager:
    """管理多个 decision_id 的日志器"""

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = Path(log_dir) if log_dir else Path("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._loggers: dict[str, DecisionLogger] = {}

    def get_logger(
        self,
        decision_id: str,
        strategy_profile_id: str = "",
        strategy_version: str = "",
    ) -> DecisionLogger:
        """获取或创建 decision_id 的日志器"""
        if decision_id not in self._loggers:
            self._loggers[decision_id] = DecisionLogger(
                log_dir=self.log_dir,
                decision_id=decision_id,
                strategy_profile_id=strategy_profile_id,
                strategy_version=strategy_version,
            )
        return self._loggers[decision_id]

    def verify_full_chain(self, decision_id: str) -> bool:
        """验证完整链路是否有日志"""
        log_file = self.log_dir / f"strategy_{decision_id}.log"
        if not log_file.exists():
            return False

        content = log_file.read_text(encoding="utf-8")
        required = [
            "provider_snapshot",
            "state_transition",
        ]
        return all(kw in content for kw in required)
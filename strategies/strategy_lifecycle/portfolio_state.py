"""本地持仓台账读取：与策略候选和日线缓存分离。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .account_risk import AccountSnapshot


@dataclass(frozen=True)
class LocalAccountState:
    reconciliation_state: str
    positions: list[dict[str, Any]] = field(default_factory=list)
    total_equity: float = 0.0
    cash: float = 0.0
    available_for_account_risk: bool = False

    def to_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            total_equity=self.total_equity,
            positions=self.positions,
            available=self.available_for_account_risk,
        )


@dataclass(frozen=True)
class PortfolioState:
    account: LocalAccountState
    execution_ledger: list[dict[str, Any]] = field(default_factory=list)
    strategy_policy: dict[str, Any] = field(default_factory=dict)


def load_portfolio_state(path: str | Path) -> PortfolioState:
    """读取本地已报告持仓；未知账户权益不参与自动账户风控。"""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("portfolio state must be a JSON object")

    raw_account = payload.get("account", {})
    if not isinstance(raw_account, dict):
        raise ValueError("portfolio state account must be an object")
    positions = raw_account.get("positions", [])
    if not isinstance(positions, list):
        raise ValueError("portfolio state positions must be a list")

    account = LocalAccountState(
        reconciliation_state=str(raw_account.get("reconciliation_state", "unverified")),
        positions=[dict(position) for position in positions if isinstance(position, dict)],
        total_equity=float(raw_account.get("total_equity") or 0.0),
        cash=float(raw_account.get("cash") or 0.0),
        available_for_account_risk=bool(raw_account.get("available_for_account_risk", False)),
    )
    ledger = payload.get("execution_ledger", [])
    policy = payload.get("strategy_policy", {})
    return PortfolioState(
        account=account,
        execution_ledger=[dict(item) for item in ledger if isinstance(item, dict)],
        strategy_policy=dict(policy) if isinstance(policy, dict) else {},
    )

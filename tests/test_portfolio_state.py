from __future__ import annotations

import json
from pathlib import Path

from strategies.strategy_lifecycle.portfolio_state import load_portfolio_state


def test_load_portfolio_state_preserves_verified_holding_and_reported_execution(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "account": {
            "reconciliation_state": "screenshot_verified",
            "positions": [{
                "code": "603019", "name": "中科曙光", "quantity": 200,
                "available_quantity": 200, "cost": 101.142,
                "status": "intraday_position_monitor",
            }],
        },
        "execution_ledger": [{
            "execution_id": "sell-003031-20260827-13180",
            "code": "003031", "side": "sell", "quantity": 100,
            "price": 131.80, "pnl_status": "loss_reported_cost_unverified",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    state = load_portfolio_state(path)

    assert state.account.positions[0]["code"] == "603019"
    assert state.account.positions[0]["quantity"] == 200
    assert state.account.positions[0]["cost"] == 101.142
    assert state.account.reconciliation_state == "screenshot_verified"
    assert state.execution_ledger[0]["pnl_status"] == "loss_reported_cost_unverified"

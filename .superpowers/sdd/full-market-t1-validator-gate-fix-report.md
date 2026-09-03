# Full-market T+1 validator gate fix report

## Scope

- Production validator: `strategies/full_market_t1.py::_validate_decision_payload`
- Regression tests: `tests/test_full_market_t1.py`
- Replay history clarification: `.superpowers/sdd/full-market-t1-task-6-report.md`
- Automation configuration changed: no
- Pre-existing dirty user files changed by this fix: no

## Root cause

The validator only applied readiness restrictions when `global_status` was exactly
`data_not_ready`. A missing value or an arbitrary value therefore bypassed both the status
contract and the not-ready action veto. The not-ready veto also inspected only each audit
row's current `entry_state`, not transitions recorded in `entry_state_trajectory`.

## RED

Tests were added before production changes for:

- missing `global_status`
- `global_status` values `None`, empty string, `ready-ish`, and integer `1`
- forged `data_not_ready` trajectories containing `entry_active` in `from_state` or `to_state`

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k "requires_valid_global_status or rejects_entry_active_in_data_not_ready_trajectory" -q
```

Observed result before the production change: `7 failed, 75 deselected`; every failure was
`Failed: DID NOT RAISE ValueError`, confirming the intended validator gaps.

## GREEN

The minimal production change:

- makes `global_status` required
- accepts only string values `decision_ready` and `data_not_ready`
- rejects `entry_active` in either endpoint of every recorded not-ready transition
- preserves the existing vetoes for `entry_state`, the three eligibility flags, and
  non-null `only_choose_one`

Focused command result: `12 passed, 70 deselected`.

Fresh verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q
82 passed, 154 warnings in 7.49s

.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q
199 passed, 174 warnings in 39.27s
```

All warnings are existing `datetime.utcnow()` deprecation warnings.

## Commit

- Implementation, tests, and Task 6 supersession note: `f7ccf5f`
- This report is committed separately so it can record the immutable implementation commit.

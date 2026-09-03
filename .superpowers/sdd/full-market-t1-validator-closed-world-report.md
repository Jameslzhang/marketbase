# Full-market T+1 validator closed-world fix report

## Scope

- Base commit: `217880d`.
- Production change: `strategies/full_market_t1.py` only.
- Regression tests: `tests/test_full_market_t1.py` only.
- Existing unrelated dirty worktree files were not modified or cleaned.

## Root cause

`_validate_decision_payload` accepted every decision beginning with `shadow_`, so
unknown actions such as `shadow_execute` passed validation.  Repeated output groups
were checked against `audit_rows` using an eight-field allowlist, leaving all other
canonical fields open to drift.

## TDD evidence

RED was established before the production change:

```text
.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q \
  -k "any_repeated_group_payload_drift or unknown_shadow_decisions"
6 failed, 91 deselected
```

The six failures were the intended `DID NOT RAISE ValueError` failures for:

- unknown decisions `shadow_execute` and `shadow_foo`;
- `reason_codes`, `execution_score`, `price`, and `executability` drift across the
  four repeated groups.

GREEN after the minimal production change:

```text
11 passed, 86 deselected
```

This focused run included the six new cases, the four prior drift cases, and the
normal consistent grouped payload.

## Implementation

- Decision values now use the explicit closed set:
  `executable_candidate`, `conditional_watch`, `reject`, `data_insufficient`,
  `shadow_watch`, and `shadow_reject`.
- Shadow membership and summary counting use the same two explicit shadow values.
- Each `executable`, `watch`, `rejected`, and `shadow` row is normalized with the
  existing `_native_mapping`/`_to_native` path and then compared as a complete
  mapping with its canonical `audit_rows` row.  No presentation-only fields are
  ignored.
- Existing group membership and executable top-three selection rules remain
  unchanged.  A mismatch reports the first differing field for actionable errors.

## Verification

```text
.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q
97 passed, 154 warnings

.venv\Scripts\python.exe -m pytest \
  tests\test_full_market_t1.py tests\test_fast_t1_scan.py \
  tests\test_local_workflow.py tests\test_strategy_lifecycle.py -q
214 passed, 174 warnings

.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q \
  -k "accepts_consistent_executable_watch_rejected_and_shadow_groups or replays_saved_2026_09_03_inputs"
2 passed, 95 deselected, 52 warnings
```

The warnings are pre-existing `datetime.utcnow()` deprecations; there were no test
failures.  The last run explicitly covers both a normal canonical payload and the
saved 2026-09-03 real-input replay.

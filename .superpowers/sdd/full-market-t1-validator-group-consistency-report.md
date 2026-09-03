# Full-market T+1 validator group-consistency fix

- Completed at: 2026-09-03 19:17:25 +08:00
- Starting HEAD: `64ac62e`
- Scope: `strategies/full_market_t1.py`, `tests/test_full_market_t1.py`

## Root cause

`_validate_decision_payload` validated row shape and aggregate counts, but it did not treat
`audit_rows` as the canonical keyed dataset for the repeated `executable`, `watch`,
`rejected`, and `shadow` views. A same-sized detached row could therefore drift from its
audit row. Under `data_not_ready`, the action veto inspected only `audit_rows`, so a
detached executable clone could retain action flags when the audit row itself was safe.

## TDD evidence

RED command:

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q -k "forged_executable_clone or zero_executable_summary or repeated_group_field_drift or group_membership or duplicate_audit_codes or consistent_executable_watch"
```

Before the implementation, 8 rejection cases failed with `DID NOT RAISE ValueError`;
the consistent normal-decision case passed.

GREEN command: the same focused command passed `9 passed, 82 deselected`.

## Implemented invariants

- `audit_rows` codes must be unique.
- Each repeated output group has the exact code set implied by the audit decision
  classification; executable exposure is the ranked top-three projection.
- Repeated rows must match their canonical audit row for `decision`, `price_band`,
  `production_buyable`, `buyable`, `only_choose_one_eligible`, `entry_state`,
  `strategy_channel`, and `dual_axis`.
- Unknown audit decision classifications are rejected.
- `data_not_ready` requires an empty `executable` group, zero
  `summary.executable`, zero `summary.executable_exposed`, and the existing global action
  vetoes remain enforced.

## Verification

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q
91 passed, 154 warnings in 5.88s

.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q
208 passed, 174 warnings in 21.44s

git diff --check -- strategies/full_market_t1.py tests/test_full_market_t1.py
exit 0 (Git emitted only the repository's LF-to-CRLF checkout notice)
```

Warnings are pre-existing `datetime.utcnow()` deprecation warnings. No unrelated dirty
workspace files were modified or staged for this fix.

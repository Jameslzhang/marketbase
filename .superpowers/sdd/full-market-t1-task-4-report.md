# Task 4 Report: Mutually exclusive T+1 decisions and only-choose-one

## RED

- Added failing contract tests in `tests/test_full_market_t1.py` for executable decisions, per-gate vetoes, `data_insufficient`, shadow non-selection, deterministic `choose_one`, and capped mutually exclusive decision groups.
- Verified RED with `.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q`.
- Observed expected failure mode: missing `evaluate_candidate`, `choose_one`, and `build_full_market_decision`.

## GREEN

- Implemented `evaluate_candidate(candidate, objective, market)`, `choose_one(rows)`, and `build_full_market_decision(candidate_union, handoff, *, decision_at)` in `strategies/full_market_t1.py`.
- Reused Task 3 `compute_execution_score` and failed closed to `data_insufficient` when execution evidence is incomplete.
- Enforced the production hard gates, canonicalized `no_chase_price` with `chase_line` alias support, kept shadow candidates permanently non-buyable, and capped exposed executable rows at three while preserving overflow as audited rejected rows.
- Verified GREEN with `.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q` -> `40 passed`.

## Commit

- Commit: `aa1fc95`
- Message: `feat: decide full-market T1 candidates`

## Staged Scope

- `strategies/full_market_t1.py`
- `tests/test_full_market_t1.py`

## Concerns

- The capped executable overflow is currently preserved by reclassifying rows into the rejected audit group with reason code `execution_group_capped`; if Task 5 expects a separate overflow bucket, that contract will need to be tightened there.
- `build_full_market_decision` accepts a deliberately small `handoff` shape (`objective_by_code` and `market`) to stay aligned with the Task 4 brief; any richer persisted fixture should preserve those keys.

## Review Fixes

- Added RED tests for explicit executability-field missing reasons, explicit `industry_sync` boolean evidence, and truth-preserving `audit_rows` with exposed executable cap only.
- Tightened candidate evaluation to fail closed when `buy_low`/`buy_high`, `no_chase_price` or `chase_line`, `protection_constructible`, `is_untradable`, or `fee_adjusted_rr` are missing or unusable.
- Removed the `advance_ratio` fallback for `industry_sync`; missing or non-boolean evidence now returns `data_insufficient`, while explicit `False` remains `industry_sync_pending`.
- Preserved real executable classification in `audit_rows`, kept `only_choose_one` based on the full executable set, and changed summary accounting to separate true executable count from exposed count.
- Re-verified with `.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q` -> `49 passed`.

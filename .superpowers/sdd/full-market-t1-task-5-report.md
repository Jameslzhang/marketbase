# Task 5 Report: Atomic full-market T1 production command

## RED

- Added failing Task 5 coverage in `tests/test_full_market_t1.py` for orchestration, manifest-path trust boundary, candidate-level `data_insufficient`, and shadow-ledger suppression on invalid finalize paths.
- Added failing Task 5 coverage in `tests/test_local_workflow.py` for the new `full-market-t1` CLI happy path, date/window contract failures, and no-fallback manifest loading.
- Initial RED results:
  - `tests/test_full_market_t1.py -k orchestrate_full_market_t1 -q`: import error for missing `orchestrate_full_market_t1`.
  - `tests/test_local_workflow.py -k "full_market_t1_cli" -q`: argparse rejected unknown `full-market-t1` command.

## GREEN

- Implemented `local_workflow.py full-market-t1 --candidate-union PATH --decision-at ISO_8601 --output PATH`.
- Implemented orchestration in `strategies/full_market_t1.py`:
  - strict manifest-only input loading from `latest_codex_input.json`;
  - trade-date and 20-minute observation-window validation;
  - objective adapter from declared snapshot/daily/classification/industry/minute files;
  - versioned decision metadata with declared input path/checksum records;
  - atomic JSON write via sibling `.tmp` + `os.replace`;
  - shadow JSONL append only after validated decision write succeeds.
- Updated/added Task 5 tests in the four allowed files only.

## Verification

- `.\.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -k "orchestrate_full_market_t1" -q`
  - `4 passed`
- `.\.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -k "full_market_t1_cli or cli_help_includes_full_market_t1" -q`
  - `5 passed`
- `.\.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py tests\test_local_workflow.py -q`
  - `84 passed` in `20.32s`
- `.\.venv\Scripts\python.exe -m pytest tests\test_strategy_lifecycle.py -q`
  - `76 passed, 19 warnings` in `1.13s`
- Earlier combined brief target suite also passed:
  - `.\.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py tests\test_local_workflow.py tests\test_strategy_lifecycle.py -q`
  - `160 passed, 19 warnings` in `16.67s`

## Commit

- Commit: `e7cf904`
- Message: `feat: orchestrate full-market T1 decisions`

## Selective staging evidence

- Commit contents (`git show --stat --oneline --name-only e7cf904`) include only:
  - `local_workflow.py`
  - `strategies/full_market_t1.py`
  - `tests/test_full_market_t1.py`
  - `tests/test_local_workflow.py`
- Residual unstaged diff after commit (`git diff --unified=0 -- tests/test_local_workflow.py`) is only the pre-existing user hunk:
  - rename of the lunch-break test;
  - assertion change from `data_not_ready/session_not_tradable` to `data_ready_static_only/minute_insufficient`.

## Concerns

- No new functional blockers in Task 5 scope.
- Existing `DeprecationWarning` entries in `tests/test_strategy_lifecycle.py` remain pre-existing and out of scope for the four allowed Task 5 files.

## Review Fix 1

- Review result: Needs fixes.
- Fix commit: `444a2b2` `fix: harden full-market T1 review gates`.
- The adapter now normalizes real run-local `timestamp` minute parquet rows (and still supports `time`/`minute`) before completed-minute lookup.
- Candidate union generation emits traceable protection constructibility only from an explicit field or the complete ordered guardrail relation `protect < buy_low <= buy_high <= no_chase_price`; orchestration never infers it merely from a protection price.
- Fee-adjusted RR is never aliased from raw `rr_ratio`. It is consumed explicitly or generated only by the versioned `cn_equity_fee_v1` formula when buy, protection, and sell-target inputs are complete.
- Decision payload validation now verifies non-empty versions and input metadata, summary integer fields and count consistency, all row-group shapes/flags/reasons, shadow false flags, and `only_choose_one` membership before writing.

### Verification after review fix

- `.venv\\Scripts\\python.exe -m pytest tests\\test_full_market_t1.py tests\\test_local_workflow.py tests\\test_strategy_lifecycle.py -q`
- Result: `170 passed, 19 warnings in 16.74s`.
- Commit scope: `strategies/full_market_t1.py`, `tests/test_full_market_t1.py`; no user-owned residual hunks were included.

## Review Fix 2

- Rereview result: Needs fixes.
- Fix commit: `ea6eef7` `fix: tighten full-market T1 payload validation`.
- `_validate_decision_payload` now cross-validates `only_choose_one`: if non-null, it must be a 6-digit code present in `audit_rows`, marked `price_band=production`, `buyable=true`, `only_choose_one_eligible=true`, `decision=executable_candidate`, and it must equal the `choose_one(...)` winner when one exists.
- Added orchestration tests that reject forged `only_choose_one` values (`999999` and non-eligible `600002`) and accept the valid winner.
- Selective staging included the Task 5 fixture hunk in `tests/test_local_workflow.py` that adds explicit `protection_constructible=True` and `fee_adjusted_rr=1.8` to `_full_market_candidate`; the separate lunch-break user hunk remained unstaged.

### Verification after rereview fix

- `.venv\\Scripts\\python.exe -m pytest tests\\test_full_market_t1.py -k "invalid_only_choose_one or valid_only_choose_one_winner" -q`
- Result: `3 passed`.
- `.venv\\Scripts\\python.exe -m pytest tests\\test_full_market_t1.py tests\\test_local_workflow.py tests\\test_strategy_lifecycle.py -q`
- Result: `173 passed, 19 warnings in 16.27s`.
- Commit contents (`git show --stat --oneline --name-only ea6eef7`) include only:
  - `strategies/full_market_t1.py`
  - `tests/test_full_market_t1.py`
  - `tests/test_local_workflow.py`

## Final Concerns

- Remaining dirty worktree state is still limited to `tests/test_local_workflow.py`; the unstaged lunch-break user hunk remains outside both review-fix commits.
- Existing `DeprecationWarning` entries in `tests/test_strategy_lifecycle.py` remain pre-existing and out of scope.

# Task 2 Report: Versioned dynamic candidate union and price bands

## Status

- DONE

## Commits

- `3ab245598a44a3ebe880bc76d451a74fa10831c0` `feat: emit dynamic T1 candidate union`

## Changed Files

- `fast_t1_scan.py`
- `strategies/full_market_t1.py`
- `tests/test_fast_t1_scan.py`
- `tests/test_full_market_t1.py`

## RED

- Command: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`
- Result: `ModuleNotFoundError: No module named 'strategies.full_market_t1'`

## GREEN

- Command: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`
- Result: `8 passed in 0.69s`
- Command: `.venv\Scripts\python.exe -m pytest tests/test_fast_t1_scan.py -q`
- Result: `8 passed in 1.03s`
- Command: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py -q`
- Result: `16 passed in 0.81s`

## Selective Staging Evidence

- Pre-commit `git diff --cached --stat` showed exactly four staged files: `fast_t1_scan.py`, `strategies/full_market_t1.py`, `tests/test_fast_t1_scan.py`, `tests/test_full_market_t1.py`.
- Pre-commit `git status --short` showed `MM fast_t1_scan.py` and `MM tests/test_fast_t1_scan.py`, confirming unrelated user changes remained unstaged while Task 2 hunks were committed from the index only.
- Post-commit `git status --short -- fast_t1_scan.py tests/test_fast_t1_scan.py strategies/full_market_t1.py tests/test_full_market_t1.py` returned only:
  - ` M fast_t1_scan.py`
  - ` M tests/test_fast_t1_scan.py`
- This confirms the commit consumed only Task 2 staged hunks and left pre-existing worktree edits untouched.

## Self-Review

- Added `strategies/full_market_t1.py` with the required constants, boundary classification, candidate union payload builder, and atomic JSON writer.
- Normalized `opportunity_tags` into `candidate_reason` lists without inventing evidence and forced all generated rows to `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`.
- Integrated `fast_t1_scan.main` to emit `candidate_union_YYYYMMDD_HHMM.json` from the same snapshot observation time used by the scan and log/print the output path.
- Added a focused fast-scan integration test proving `main` passes `trade_date`, `observed_at`, `market_rows`, and `funnel` into candidate-union generation without hitting live providers.

## Concerns

- `fast_t1_scan.py` and `tests/test_fast_t1_scan.py` still contain unrelated unstaged changes from outside Task 2 by design; they were not reverted or included in the commit.

## Review Fix 1

- Review result: Needs fixes
- Issue: `fast_t1_scan.py` passed `out_df` into `build_candidate_union`, so the candidate union dropped complete scan evidence that existed on `candidates`.
- Commit: `0d0adad76c57b28228869fa159a1252c040a793b` `fix: preserve full candidate evidence in union`

### RED

- Command: `.venv\Scripts\python.exe -m pytest tests/test_fast_t1_scan.py::test_main_writes_candidate_union_with_scan_metadata -q`
- Result: `AssertionError` because `captured["frame"].columns` did not include `ma10`, `ma20`, `return_10d`, or `elapsed_trade_minutes`.

### GREEN

- Command: `.venv\Scripts\python.exe -m pytest tests/test_fast_t1_scan.py::test_main_writes_candidate_union_with_scan_metadata -q`
- Result: `1 passed in 1.77s`
- Command: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py -q`
- Result: `16 passed in 1.40s`

### Selective Staging Evidence

- `git diff --cached -- fast_t1_scan.py tests/test_fast_t1_scan.py` showed only two staged hunks:
  - `build_candidate_union(out_df, ...)` -> `build_candidate_union(candidates.copy(), ...)`
  - the new integration assertion requiring `ma10`, `ma20`, `return_10d`, and `elapsed_trade_minutes`
- Post-commit `git status --short -- fast_t1_scan.py tests/test_fast_t1_scan.py` returned only:
  - ` M fast_t1_scan.py`
  - ` M tests/test_fast_t1_scan.py`
- This confirms pre-existing dirty hunks remained unstaged and were not included in the fix commit.

## Review Fix 2

- Review result: Needs fixes
- Issue: the regression test patched `load_or_compute_indicators`, which existed only in the dirty worktree and not in the exact reviewed commit, causing an isolated checkout to fail before reaching the candidate-union path.
- Commit: `e46d30d8db9fb53bd1692b7655e40174d1b511d1` `fix: stabilize task2 candidate union regression test`
- Fix: always patch the committed `compute_indicators_parallel` interface and conditionally patch `load_or_compute_indicators` only when the working tree exposes it.

### Verification

- Current authorized dirty workspace: `.venv\\Scripts\\python.exe -m pytest tests\\test_full_market_t1.py tests\\test_fast_t1_scan.py -q` -> `16 passed in 0.93s`.
- Detached clean worktree at exact `e46d30d8db9fb53bd1692b7655e40174d1b511d1`: `D:\\Environment\\marketbase\\.venv\\Scripts\\python.exe -m pytest tests\\test_full_market_t1.py tests\\test_fast_t1_scan.py -q` -> `14 passed in 1.42s`.
- The clean checkout has two fewer pre-existing dirty-worktree tests, but all committed Task 2 tests pass with zero failures.

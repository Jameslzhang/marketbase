# Full-market T+1 Task 1 report

- Run time: 2026-09-03 15:13:29 +08:00
- Status: `DONE`
- Commits: `ed3a1a177e918bcfd20feec7f19cd9a4c5096036`, `af0741725fe2b832cd99a521f298ed169100c4c7`, `bd00aec32cea61c574695c8d014dbf19a91fff45`

## Changed files

- `marketbase/pipeline/steps.py`
- `marketbase/pipeline/quality.py`
- `tests/test_local_workflow.py`

## TDD evidence

RED command:

```powershell
python -m pytest tests/test_local_workflow.py -k "minute_snapshot_audits_run_local_parquet_before_shared_cache or minute_quality_prefers_dynamic_expected_minutes_for_full_threshold" -q
```

Expected failure observed: the run-local regression audited the shared snapshot cache and returned `actual_minutes == 1` instead of `3`; the dynamic expected-minute regression returned `partial` instead of `full`.

GREEN command:

```powershell
python -m pytest tests/test_local_workflow.py -k "minute_snapshot_audits_run_local_parquet_before_shared_cache or minute_quality_prefers_dynamic_expected_minutes_for_full_threshold" -q
```

Result: `2 passed, 24 deselected in 1.27s`.

Required suite command:

```powershell
python -m pytest tests/test_local_workflow.py -q
```

Exact result: `3 failed, 23 passed in 74.74s (0:01:14)`.

## Implementation and self-review

- The shared `cache/intraday_1m.parquet` append remains intact for history.
- When a run-local minute parquet exists, the audit reads it, normalizes collector `timestamp`/`close` columns to `time`/`price`, and audits that sequence rather than the shared snapshot cache.
- `minute_audit["collection_audit"]` receives an independent copy of the collector audit.
- Minute quality uses `expected_minutes_dynamic` first, with the required full-quality threshold formula and fallback to `total_expected_minutes`.
- `git diff --cached --check` passed before commit. Selective staging confirmed the commit contains only this task's three source/test files; all pre-existing unstaged hunks remain unstaged.

## Deterministic fixture fix pass

The test-only autouse fixture supplies a valid run-local minute parquet for intraday phases and a fixture index result. It monkeypatches only test execution and leaves production dependencies unchanged.

RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -q
```

Result: `3 failed, 23 passed in 119.63s (0:01:59)`. The failures were caused by live minute/index provider fall-through and its all-null local minute timestamps.

GREEN commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -k "minute_snapshot_audits_run_local_parquet_before_shared_cache or minute_quality_prefers_dynamic_expected_minutes_for_full_threshold" -q
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -q
```

Exact results: `2 passed, 24 deselected in 1.27s`; `26 passed in 18.62s`.

Selective staging for commit `af0741725fe2b832cd99a521f298ed169100c4c7` contained only the deterministic fixture hunk in `tests/test_local_workflow.py`; pre-existing unstaged changes remain unstaged.

## Concerns

- Ruff remains unavailable in the active Python environment (`No module named ruff`); the requested pytest verification is clean.

## Review fix pass

Commit `bd00aec32cea61c574695c8d014dbf19a91fff45` fails closed when a requested run-local parquet is absent: it writes a `sequence_error`, preserves the history append, and does not audit or derive facts from the shared cache. It also replaces the module-wide autouse provider patch with an explicitly requested fixture on only collection tests, and centralizes run-local parquet normalization.

RED command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -k minute_snapshot_rejects_missing_run_local_parquet_without_cache_audit -q
```

Exact result: `1 failed, 26 deselected in 8.98s`; the old behavior incorrectly populated `sequence_audit` from the shared cache.

GREEN commands:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -k "minute_snapshot_audits_run_local_parquet_before_shared_cache or minute_snapshot_rejects_missing_run_local_parquet_without_cache_audit or minute_quality_prefers_dynamic_expected_minutes_for_full_threshold" -q
.\.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -q
```

Exact results: `3 passed, 24 deselected in 1.70s`; `27 passed in 18.38s`.

`git diff --cached --check` and `git show --check` passed. Selective staging included only `marketbase/pipeline/steps.py` and `tests/test_local_workflow.py` task hunks; existing dirty hunks remain unstaged.

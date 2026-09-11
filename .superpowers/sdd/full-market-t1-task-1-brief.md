# Task 1 Brief: Correct run-local minute continuity auditing

This task is part of `docs/superpowers/plans/2026-09-03-full-market-t1-execution-pipeline.md`.

## Global constraints

- Production visible candidates remain Shanghai/Shenzhen main-board stocks priced at least CNY 50.00.
- Stocks priced CNY 40.00–49.99 are shadow-only and always have `production_buyable=false`, `buyable=false`, and `only_choose_one_eligible=false`.
- Do not lower opportunity 65 or execution 68 thresholds.
- A single quote snapshot never satisfies a continuous-minute gate.
- Missing candidate industry, daily, minute, VWAP, or executability evidence yields candidate-level `data_insufficient`; do not estimate missing values.
- Only market-wide critical failures produce global `data_not_ready`.
- Preserve all pre-existing uncommitted work. `marketbase/pipeline/quality.py` and `tests/test_local_workflow.py` already contain unrelated unstaged changes. Do not revert them and do not include those pre-existing hunks in this task commit.

## Files

- Modify: `marketbase/pipeline/steps.py`, function `_run_minute_snapshot`
- Modify: `marketbase/pipeline/quality.py`, function `_compute_minute_quality`
- Modify: `tests/test_local_workflow.py`

## Required behavior

1. Add a regression test that creates a run-local parquet containing three distinct completed minutes for two codes while the shared cache contains only one appended snapshot minute.
2. Observe the new test fail because current code audits `cache/intraday_1m.parquet`.
3. Preserve the shared-cache append for history, but audit the normalized run-local parquet whenever `intraday_minutes_path` exists.
4. Store a copy of the collector audit under `minute_audit["collection_audit"]`.
5. In `_compute_minute_quality`, prefer `expected_minutes_dynamic`; fall back to `total_expected_minutes`.
6. Full-quality threshold must be computed as:

```python
expected = int(seq_audit.get("expected_minutes_dynamic") or seq_audit.get("total_expected_minutes", 240))
minimum_full = max(int(expected * 0.83), min(200, expected))
if actual >= minimum_full and missing <= 40 and breaks <= 3:
    return "full"
```

7. Run `tests/test_local_workflow.py` and report the exact result.
8. Commit the task. Because two files already have unrelated unstaged hunks, use selective staging and verify `git diff --cached` contains only this task. Do not stage any other existing change.

## Test intent

The regression must prove that the sequence audit returns three minutes from the run-local parquet rather than one minute from the shared snapshot cache. Use real parquet IO when pyarrow is available; follow the repository's existing skip convention otherwise.

## Report contract

Write the full report to `.superpowers/sdd/full-market-t1-task-1-report.md`. Include:

- status: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`;
- files changed and commit hash;
- RED command and expected failure;
- GREEN command and exact pass count;
- confirmation that pre-existing unstaged hunks remain unstaged;
- self-review findings and concerns.

Return only status, commit hash, one-line test summary, and concerns.

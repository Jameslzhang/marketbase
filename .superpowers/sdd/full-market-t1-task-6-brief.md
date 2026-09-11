# Task 6 Brief: Deterministic 2026-09-03 replay

## Repository and scope

- Repository: `D:\Environment\marketbase`
- Starting commit: `ea6eef7`
- Create `tests/fixtures/full_market_t1/2026-09-03/README.md`.
- Create `tests/fixtures/full_market_t1/2026-09-03/expected_summary.json`.
- Modify `tests/test_full_market_t1.py`.
- Modify `strategies/full_market_t1.py` only if the real replay exposes a production semantic defect; any such change must be minimal, TDD-backed, and documented.
- Preserve all dirty user changes and selectively stage only Task 6 hunks.

## Saved sources

- Objective run: `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data`.
- Scan CSV: `data/cache/fast/scan_result_20260903_1353.csv` (36 candidates).
- Do not copy the large market JSON/CSV/parquet files into Git.
- README must record each consumed source relative path, byte size, SHA-256, observation-time interpretation, and the replay command/test.

## Replay adapter/test

- Add a deterministic test fixture/helper that reads the saved CSV, calls the production `build_candidate_union` using `trade_date=2026-09-03`, `observed_at=2026-09-03T13:53:00+08:00`, `market_rows=5546`, and an auditable funnel, then calls the production orchestration/decision path.
- Create a temporary `data_root/latest_codex_input.json` whose declared paths point only to the saved run files. Use the saved run manifest `generated_at=2026-09-03T13:46:56.819085+08:00`; the 6-minute difference is within the 20-minute contract.
- Required declared files come from that saved run: `market_snapshot.json`, `daily_indicators.csv`, `classification_map.csv`, `industry_agg.csv`, `market_breadth.json`, `intraday_minutes.parquet`, `data_audit.json`.
- The helper may use a temp candidate-union/decision/ledger, but must not mutate the saved run or current production `latest_codex_input.json`.
- If the large external saved sources are absent in another checkout, skip this integration replay with a clear reason; when present, verify README hashes before evaluating so drift fails loudly.

## Expected semantics

- `global_status == "decision_ready"`; `classification_coverage_insufficient` is a warning/partial quality, not a global veto.
- `only_choose_one is None`.
- `summary.executable == 0` and `summary.executable_exposed == 0`.
- `summary.shadow_count >= 1` (the saved CSV currently contains main-board 40-49.99 candidates; freeze exact count if replay confirms it).
- All non-shadow, non-executable rows retained in audit/rejected/watch groups must have explicit reason codes; no silent empty rejection.
- Every shadow row keeps `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`.
- Market rows/funnel remain 5546-source traceable.

`expected_summary.json` must at minimum freeze:

```json
{
  "trade_date": "2026-09-03",
  "market_rows": 5546,
  "only_choose_one": null,
  "executable": 0,
  "global_status": "decision_ready"
}
```

## TDD and verification

First add the replay test and observe RED or semantic mismatch. Then add only the needed replay adapter/test manifest (and minimal production fix if real evidence requires it).

Run replay test alone, then:

`.venv\Scripts\python.exe -m pytest -q`

The full suite must report zero failures. If the full suite produces no output for more than four minutes, stop it, record that bounded limitation, and run the four target suites:

`.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py tests\test_fast_t1_scan.py tests\test_local_workflow.py tests\test_strategy_lifecycle.py -q`

Commit Task 6 files with message `test: replay full-market T1 decision`. Write report to `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-6-report.md` including source hashes, actual summary, RED/GREEN, verification, commit, and concerns.

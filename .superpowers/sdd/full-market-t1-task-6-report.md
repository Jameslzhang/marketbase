# Task 6 Report: deterministic 2026-09-03 replay

## Scope

- Starting commit: `ea6eef7`
- Allowed code changes used: `tests/test_full_market_t1.py`
- Added fixtures:
  - `tests/fixtures/full_market_t1/2026-09-03/README.md`
  - `tests/fixtures/full_market_t1/2026-09-03/expected_summary.json`
- Production files changed: none
- Production `latest_codex_input.json` changed: no
- Trading actions taken: none

## RED

- Added `test_orchestrate_full_market_t1_replays_saved_2026_09_03_inputs`.
- Verified the new test failed first because `tests/fixtures/full_market_t1/2026-09-03/expected_summary.json` did not exist yet.
- Command:
  - `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k replays_saved_2026_09_03_inputs -q`
- Failure summary:
  - `FileNotFoundError: tests\fixtures\full_market_t1\2026-09-03\expected_summary.json`

## GREEN

- Added deterministic replay helpers that:
  - load the saved `scan_result_20260903_1353.csv`
  - rebuild the candidate union with `trade_date=2026-09-03`, `observed_at=2026-09-03T13:53:00+08:00`, `market_rows=5546`
  - write a temporary `latest_codex_input.json` that points only at the saved run files
  - verify the saved source hashes recorded in the fixture README before evaluation
  - skip cleanly in another checkout if the large saved inputs are absent
- Added fixture documentation plus frozen expected summary.
- No production semantic defect was exposed, so `strategies/full_market_t1.py` was left unchanged.

## Actual replay summary

```json
{
  "trade_date": "2026-09-03",
  "market_rows": 5546,
  "only_choose_one": null,
  "global_status": "decision_ready",
  "summary": {
    "executable": 0,
    "executable_exposed": 0,
    "watch": 2,
    "rejected": 33,
    "shadow_count": 1,
    "evaluated": 36
  },
  "shadow_codes": [
    "600362"
  ],
  "data_audit_quality_status": "partial"
}
```

## Verification

- Replay test:
  - `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k replays_saved_2026_09_03_inputs -q`
  - Result: `1 passed, 66 deselected`
- Full suite bounded limitation:
  - `.venv\Scripts\python.exe -m pytest -q`
  - Interrupted after exceeding the brief's no-output boundary; tty surfaced `KeyboardInterrupt` during collection in `marketbase/classification_collector.py` and reported `no tests ran in 104.47s`.
- Required target suites:
  - `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q`
  - Result: `182 passed, 19 warnings in 18.53s`

## Source hashes

- `data/cache/fast/scan_result_20260903_1353.csv`
  - bytes: `13074`
  - sha256: `951832096c579d287d19a06cea44dce7b90fb304ceeccdb8983e27912cd627f1`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/market_snapshot.json`
  - bytes: `12042899`
  - sha256: `bce2c0efc5a7333bf3329828f3b883075f0e168be7356846a319cae6450752ac`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/daily_indicators.csv`
  - bytes: `3121082`
  - sha256: `e567ab577bc4722ab43863258f5ad18b1f60a1a4afab6f38ec3ed0267abdfa77`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/classification_map.csv`
  - bytes: `972381`
  - sha256: `4450f63decf5616414611a1623c46ad025bd137d59676c4bba42ef76b6ab9de6`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/industry_agg.csv`
  - bytes: `9269`
  - sha256: `1d606637d9978949dd2d2762d574007d597e2c349fb6b677134215f4a0a5da4d`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/market_breadth.json`
  - bytes: `29925`
  - sha256: `0283a877a81ca16bbfae75bc9611ebe9eae15d78102a3de9ff64ae88b341c995`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/intraday_minutes.parquet`
  - bytes: `17589118`
  - sha256: `5f2a8660be950a75c494561878156affc172d581615c0469edfa32290891ea4f`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/data_audit.json`
  - bytes: `824500`
  - sha256: `97aca8550074449cde2611169f10ee18e4bdde9cacf332723b4de915b7592374`
- `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/manifest.json`
  - bytes: `3892`
  - sha256: `556fefbca1c84b5085ed496116540dd623fc11a1a0370d40664712caa6c89dea`

## Commit

- Commit message: `test: replay full-market T1 decision`

## Concerns

- The full repository-wide `pytest -q` path did not provide bounded, timely output in this environment, so Task 6 relied on the required four target suites after interrupting the silent run.

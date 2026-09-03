# 2026-09-03 deterministic replay

This fixture freezes the real full-market T+1 replay inputs used by Task 6 without copying large source data into Git.

## Observation-time interpretation

- Candidate scan source: `data/cache/fast/scan_result_20260903_1353.csv`
- Candidate union `observed_at`: `2026-09-03T13:53:00+08:00`
- Saved run manifest `generated_at`: `2026-09-03T13:46:56.819085+08:00`
- Contract interpretation: the candidate scan was observed about 6 minutes after the saved run manifest generation time, which stays within the 20-minute handoff contract enforced by `strategies.full_market_t1._validate_contract`.
- Replay decision time: `2026-09-03T13:53:00+08:00`
- Replay test: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k replays_saved_2026_09_03_inputs -q`

## Consumed source records

<!-- task6-source-records:start -->
```json
[
  {
    "path": "data/cache/fast/scan_result_20260903_1353.csv",
    "bytes": 13074,
    "sha256": "951832096c579d287d19a06cea44dce7b90fb304ceeccdb8983e27912cd627f1"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/market_snapshot.json",
    "bytes": 12042899,
    "sha256": "bce2c0efc5a7333bf3329828f3b883075f0e168be7356846a319cae6450752ac"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/daily_indicators.csv",
    "bytes": 3121082,
    "sha256": "e567ab577bc4722ab43863258f5ad18b1f60a1a4afab6f38ec3ed0267abdfa77"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/classification_map.csv",
    "bytes": 972381,
    "sha256": "4450f63decf5616414611a1623c46ad025bd137d59676c4bba42ef76b6ab9de6"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/industry_agg.csv",
    "bytes": 9269,
    "sha256": "1d606637d9978949dd2d2762d574007d597e2c349fb6b677134215f4a0a5da4d"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/market_breadth.json",
    "bytes": 29925,
    "sha256": "0283a877a81ca16bbfae75bc9611ebe9eae15d78102a3de9ff64ae88b341c995"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/intraday_minutes.parquet",
    "bytes": 17589118,
    "sha256": "5f2a8660be950a75c494561878156affc172d581615c0469edfa32290891ea4f"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/data_audit.json",
    "bytes": 824500,
    "sha256": "97aca8550074449cde2611169f10ee18e4bdde9cacf332723b4de915b7592374"
  },
  {
    "path": "data/daily_runs/2026-09-03/134656_intraday_1300_objective_data/manifest.json",
    "bytes": 3892,
    "sha256": "556fefbca1c84b5085ed496116540dd623fc11a1a0370d40664712caa6c89dea"
  }
]
```
<!-- task6-source-records:end -->

# 2026-09-03 deterministic replay

This fixture freezes the real full-market T+1 replay inputs used by Task 6 without copying large source data into Git.

## Observation-time interpretation

- Candidate scan source: `data/cache/fast/scan_result_20260903_1441.csv`
- Candidate union `observed_at`: `2026-09-03T14:41:00+08:00`
- Saved run manifest `generated_at`: `2026-09-03T14:43:09.708426+08:00`
- Candidate lifecycle columns are deterministically enriched from that exact run's
  `daily_indicators.csv`, matching the current scanner output contract.
- Frozen `data_audit.quality_reason_codes`: `["classification_coverage_insufficient"]`
- Contract interpretation: the candidate scan and objective run are about 2 minutes apart,
  within the 20-minute handoff contract enforced by `strategies.full_market_t1._validate_contract`.
- Replay decision time: `2026-09-03T14:43:00+08:00`
- Replay test: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k replays_saved_2026_09_03_inputs -q`

## Consumed source records

<!-- task6-source-records:start -->
```json
[
  {
    "path": "data/cache/fast/scan_result_20260903_1441.csv",
    "bytes": 18235,
    "sha256": "1050be87f47634452de3463820a1013a623bc9813f38008b22819d8646a4887a"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/market_snapshot.json",
    "bytes": 12043064,
    "sha256": "a75c07d78460a2b82cf7ae0cbe8c417b909ab0d1b601dbee36322a9ad349b9ea"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/daily_indicators.csv",
    "bytes": 3120922,
    "sha256": "41e3f13a0404fe908d3b164accfe9c6dccb5e8789457f636ea03d5228f5f8b45"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/classification_map.csv",
    "bytes": 972381,
    "sha256": "4450f63decf5616414611a1623c46ad025bd137d59676c4bba42ef76b6ab9de6"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/industry_agg.csv",
    "bytes": 9284,
    "sha256": "d5c5bc9687ef9ea151d8cf6fd06283371ab58a815f7d8f16047829a67c53fa3d"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/market_breadth.json",
    "bytes": 29909,
    "sha256": "e27c0580744e1f749e7fca611448aed6102d45a7dece029b9e9d3d47895f47fb"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/intraday_minutes.parquet",
    "bytes": 4898075,
    "sha256": "91f7d82dcf949a321820ae2cb1a297613dfa3d9af0a053df2f6870171902d140"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/data_audit.json",
    "bytes": 861717,
    "sha256": "eb8244cbdbf7e8a31a174d5284c4dab30581f7da4904309788c3275223c9ba6a"
  },
  {
    "path": "data/daily_runs/2026-09-03/144309_intraday_1430_objective_data/manifest.json",
    "bytes": 3892,
    "sha256": "28209109b0441b65de5346a0c43863eb179b1d9984e31f4152fe3cc0e54c2a64"
  }
]
```
<!-- task6-source-records:end -->

# Full-market T1 release semantics fix

## Scope

Follow-up to `e191847`. This change closes readiness propagation, ATR units,
full-cross-section RPS20, frozen-channel naming, and deterministic replay evidence.
The published V2 rule document was not modified.

## RED evidence

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -k "buyable_requires_both_scores or handoff_not_ready or actions_in_data_not_ready or replays_saved" tests/test_fast_t1_scan.py -k "rps20_uses_full or buyable_requires_both_scores or handoff_not_ready or actions_in_data_not_ready or replays_saved" -q
```

Observed: `9 failed`. Failures proved that handoff readiness did not reach the
lifecycle, the validator accepted forbidden actions under `data_not_ready`, the
frozen channel names and mapping metadata were absent, ATR percent points were
passed directly to a decimal-ratio consumer, RPS was ranked only on the candidate
subset, and the saved replay never exercised DualAxis.

A second focused RED (`test_rps20_universe_keeps_low_price_and_low_liquidity_but_excludes_zero_volume`)
failed with missing `select_rps20_universe`; it freezes “tradable” as positive
volume while retaining names below the candidate price/liquidity thresholds.

## Implemented contracts

- `handoff.critical_ready` and `market.critical_ready` are combined before every
  candidate evaluation. A false value reaches DualAxis as `market_veto` and cannot
  produce `entry_active` or any production eligibility flag.
- Validator rejects `data_not_ready` payloads containing `entry_active`, any true
  eligibility flag, or non-null `only_choose_one`.
- Persisted daily/candidate `atr14_pct` uses `percent_points` (`2.0 == 2%`). The
  lifecycle adapter explicitly converts to `decimal_ratio` (`0.02`). Unit-tagged
  decimal fixtures and legacy direct inputs are not divided twice.
- RPS20 is ranked once on all valid tradable Shanghai/Shenzhen main-board names
  after security-eligibility filters but before price and liquidity filters, then
  mapped back to candidates. The optional uncommitted indicator cache is used when
  present; clean checkout falls back to direct indicator computation.
- Output `strategy_channel` uses frozen V2 values and preserves the raw enum as
  `lifecycle_channel`, with `channel_mapping_version=frozen_v2_to_lifecycle_v1`.

## Replay fixture source

The replay now uses the saved `2026-09-03 14:41` fast scan and the exact
`144309_intraday_1430_objective_data` run (manifest `14:43:09+08:00`). The scan is
deterministically enriched with lifecycle columns from that run's hashed
`daily_indicators.csv`, equivalent to the current scanner artifact contract.
The replay has 51 audit rows and includes code `600698` with
`dual_axis.status=evaluated`, complete channel mapping and state trajectory.

## GREEN evidence

Focused regression: `9 passed`.

Target suite:

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q
```

Observed in the working tree: `192 passed`; warnings are existing UTC
deprecations. A detached clean checkout of the commit produced `189 passed,
1 skipped`; the skip is the replay whose large ignored source files exist only in
the primary workspace. The same replay was run separately there and passed.

Commit: recorded in Git history for this report and implementation.

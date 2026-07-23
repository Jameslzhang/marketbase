# Task 4 Report: Objective Live-Market Collection and Audit

## Implementation Summary

- Added `marketbase.market_collector` with the frozen `MarketCollectionResult`
  result type and `collect_market_snapshot` public API.
- Reused `acquire_live_snapshot` for Sina live quotes and
  `fetch_reference_snapshot_with_bse_fallback` for the efinance /
  em_datacenter + Tencent BSE reference path.
- Normalized output to the fixed objective fields only, with deterministic
  `sh`, `sz`, `bj`, then code ordering. Cached rows are supplied only as a BSE
  code-universe reference; cached prices are never returned as live prices.
- Added `marketbase.data_audit` for coverage, field completeness, duplicates,
  invalid codes, stale rows, known zero-volume and unavailable-volume-ratio
  conditions, and neutralized provider errors.
- Collection returns auditable Shanghai/Shenzhen partial results when the BSE
  reference path fails. It raises clearly when no usable market rows remain.
- Cache writes use a UTF-8 JSON temporary file and `os.replace`; a failed
  replacement leaves the prior cache untouched.

## RED / GREEN

1. RED: `tests/test_data_audit.py` could not import the absent
   `marketbase.data_audit` module.
   GREEN: implemented objective audit metrics; 2 tests passed.
2. RED: `tests/test_market_collector.py` could not import the absent
   `marketbase.market_collector` module.
   GREEN: implemented collection, fixed output schema, source attribution,
   fallback handling, progress evidence, and atomic cache protection; 4 tests
   passed.
3. RED: a BSE reference error containing `buy` leaked into the public report.
   GREEN: the report now exposes only neutralized `provider_errors`; related
   collector and audit tests passed.
4. RED: a primary response containing no usable six-digit market code returned
   an empty result.
   GREEN: collection now raises `RuntimeError` with `no usable market rows`;
   all 7 new-module tests passed.

## Changed Files

- `marketbase/market_collector.py` (new)
- `marketbase/data_audit.py` (new)
- `tests/test_market_collector.py` (new)
- `tests/test_data_audit.py` (new)
- `.superpowers/sdd/task-4-report.md` (new)

`marketbase/live_workflow.py` and `tests/test_live_workflow.py` were reviewed
but not modified. No candidate, rank, recommendation, or trading logic was
imported or changed. No Git commit was created.

## Verification

Executed on 2026-07-22:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_market_collector.py tests\test_data_audit.py tests\test_live_workflow.py tests\test_snapshot.py -q
41 passed in 2.00s

.\.venv\Scripts\python.exe -m ruff check marketbase\market_collector.py marketbase\data_audit.py marketbase\live_workflow.py tests\test_market_collector.py tests\test_data_audit.py
All checks passed!
```

## Residual Risks

- Live upstream availability, rate limits, and provider schema changes remain
  external risks. The collector records provider errors and returns current
  mainland rows when that remains possible.
- On a first run with no cached BSE code universe, an efinance outage can leave
  BSE uncovered until a current BSE reference source is available. The
  collector records this as a coverage gap and does not substitute cached
  prices.
- Stale-row detection uses each row's `observed_at` timestamp and a 15-minute
  threshold; it deliberately does not infer freshness from names, price moves,
  or strategy criteria.

## Final audit-accounting fix

- Market coverage is now calculated exclusively from the cleaned final frame;
  raw upstream validity masks are no longer index-aligned against reordered rows.
- Duplicate and invalid code counts are calculated independently for primary and
  reference sources, then attached as audit evidence. Normal overlap between the
  two providers is not counted as a duplicate.

## Independent Review Remediation (2026-07-22)

### RED / GREEN

1. RED: a Tencent BSE refresh retained cache-origin `source`, quote metadata,
   volume ratio, and observation timestamps.  GREEN: the refresh now removes
   cache-only quote metadata before copying a row, sets `source` to
   `tencent_bse`, uses the current Tencent tick time for both time fields, and
   records an unavailable volume ratio as `NaN`.
2. RED: the collector audited only its final filtered frame, hiding upstream
   duplicate and invalid codes.  GREEN: the final output remains one legal,
   unique row per code while the audit receives normalized upstream code
   evidence through a backward-compatible optional argument.
3. RED: replacing every output row's observation time with the collection time
   made an old intraday quote look fresh.  GREEN: stale detection now combines
   a supplier time-of-day with the current observation date and falls back to
   an actual row observation time only when no supplier quote time is usable.
4. RED: cache replacement errors entered the report unchanged.  GREEN: cache
   errors use the same neutralization as provider errors, and progress only
   emits neutral error data or fixed collection-stage text.

### Changed Files

- `marketbase/live_workflow.py`
- `marketbase/market_collector.py`
- `marketbase/data_audit.py`
- `tests/test_live_workflow.py`
- `tests/test_market_collector.py`
- `.superpowers/sdd/task-4-report.md`

### Verification

- RED command: `pytest tests/test_market_collector.py tests/test_live_workflow.py -q`
  produced the four expected failures for upstream audit facts, stale supplier
  quote time, cache-error neutralization, and Tencent cache metadata.
- GREEN command: `pytest tests/test_market_collector.py tests/test_data_audit.py tests/test_live_workflow.py -q`
  passed 25 tests.
- `ruff check` passed for all Task 4 implementation and test files.

### Residual Risks

- Supplier time-only values are interpreted on the collection date. A provider
  that publishes a delayed prior-day time without a date can therefore only be
  distinguished by its time-of-day age during the active collection window.

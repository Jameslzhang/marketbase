# Task 8 Report

## Scope

Task 8 completes the local objective-data boundary. The working tree was already
substantially changed when this handoff began; those existing changes were
preserved. No Git commit was created.

## Dependency Audit

local_workflow.py imports only the retained objective modules:
classification_map, daily, daily_collector, data_request, market_collector,
minute_collector, and neutral_indicators. The retained package API also exposes
snapshot acquisition, live-snapshot helpers, audit utilities, and source guards.
There are no imports that keep removed local decision modules reachable.

## Retained Components

- classification_map.py, daily.py, daily_collector.py, data_audit.py
- data_request.py, live_workflow.py, market_collector.py,
  minute_collector.py, neutral_indicators.py, snapshot.py, and source_guard.py
- local_workflow.py, objective-only package exports, and the two-command CLI
- tests for the retained collection, request, audit, mapping, indicator,
  snapshot, live, and package interfaces

## Removed Or Cleaned

- Local decision, scoring, ranking, strategy, persistence, server, US-snapshot,
  and related test modules already listed as deletions in the task working tree
- Strategy YAML trees under both strategies/ and marketbase/strategies/
- The now-empty marketbase/strategies/ directory
- The remaining disabled US/yfinance test block in tests/test_snapshot.py
- Legacy configuration in .env.example, agent metadata, and the three
  top-level documents

The rewritten README.md, README.zh-CN.md, and SKILL.md describe only the data
boundary, collection, request protocol, handoff files, caches, limits, and
disclaimer. .env.example now contains only optional data-provider and timeout
settings. agents/openai.yaml describes objective data collection.

## Baostock Regression

Added a parameterized regression test for 430047, 830001, and 920001.
Before the fix, Baostock code conversion incorrectly sent the first two to sz.
and the last to sh. _to_baostock_code now raises a clear ValueError for the BSE
prefixes 4, 8, and 920.

Focused verification: pytest -q tests/test_daily.py -k baostock
Result: 5 passed, 14 deselected.

## Verification

- python -m pytest -q: 160 passed in 10.13s
- python -m ruff check .: passed
- python -m build: passed after installing the missing build verification module
  into the existing .venv; built sdist and wheel
- git diff --check: passed after removing trailing blank lines
- python -m marketbase.cli --help: passed; lists only collect and fulfill-request
- python local_workflow.py fulfill-request --help: passed
- Temporary venv wheel smoke: installed
  dist/marketbase-0.2.0-py3-none-any.whl and marketbase --help passed. The
  temporary environment is under D:\Codex\99_临时区\V0722*_临时安装验证.
- Offline end-to-end:
  test_run_collection_collects_every_market_code_and_writes_only_protocol_files
  and test_fulfill_request_reads_only_requested_codes_and_writes_response both
  passed. They verify the seven run files, latest_codex_input.json, and standard
  JSON request/response handling.

## Real Data Smoke

A single Sina real-time snapshot smoke was run with
MARKETBASE_SNAPSHOT_CALL_TIMEOUT_SEC=12 and a 45-second process limit. It did not
request daily history. It completed in 12.8 seconds and returned 5,199 rows
(600000, 600004, 600006 were the first codes).

## Residual Risks

- External providers can rate-limit, fail, delay, or return partial data; inspect
  data_audit.json and source errors for every real run.
- The real smoke validates only one snapshot provider at one point in time, not
  a complete multi-provider outage scenario.
- The required full-market 250-day collection is intentionally not run here
  because it can take hours and trigger provider limits. Cache, checkpoint,
  progress, ETA, and failure-isolation behavior are covered by offline tests.


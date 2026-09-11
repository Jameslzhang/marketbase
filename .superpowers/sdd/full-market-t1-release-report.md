# Full-Market T+1 Execution Pipeline Release Report

Released: 2026-09-03 20:58:02 +08:00

Status: approved by final independent release review.

## Scope

- Design: `docs/superpowers/specs/2026-09-03-full-market-t1-execution-pipeline-design.md`
- Plan: `docs/superpowers/plans/2026-09-03-full-market-t1-execution-pipeline.md`
- Implementation range: `016c38b..ed35e5b`
- Tasks 1 through 8 completed, with independent reviews and RED-GREEN remediation of every release blocker.

## Verification

- Final bounded regression command:
  `.venv\\Scripts\\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q`
- Result: `214 passed, 174 warnings in 18.55s` on the final working tree.
- The warnings are existing `datetime.utcnow()` deprecation warnings amplified by the now-exercised lifecycle transitions; no test failed.
- Unbounded repository-wide `pytest -q` had previously stalled during collection, so the plan-defined target suite is the release gate.

## Real replay

- Trade date: 2026-09-03.
- `global_status=decision_ready`.
- `only_choose_one=null`.
- Summary: `executable=0`, `executable_exposed=0`, `watch=3`, `rejected=43`, `shadow_count=5`, `evaluated=51`.
- At least one saved candidate (`600698`) completes real `DualAxis` evaluation with a non-empty lifecycle trajectory.
- Interpretation: the empty executable set is an audited market outcome, not missing data or a silent pipeline failure.

## Release hardening

- The real fast-scan path preserves CNY 40.00-49.99 for shadow audit, includes CNY 50.00 in production, and excludes prices below CNY 40.00.
- Global `data_not_ready` is fail-closed through lifecycle state, eligibility flags, `only_choose_one`, executable views, and summary counts.
- ATR percentage points are explicitly converted to the lifecycle ratio contract; RPS20 is ranked on the pre-price/pre-liquidity tradable main-board cross-section.
- Existing `ChannelIdentifier`, `DualAxis`, and `EntryStateMachine` implementations are reused. Frozen V2 channel names are emitted through a versioned mapping.
- `global_status` and decisions are closed enums. `audit_rows` is the canonical fact set and every repeated output group must match its audit row after normalization.

## Frozen rules and rollback

- V2: `D:\\Codex\\02_正式交付区\\V09031808_A股T1四轮定时任务冻结总规则第2版.md`
- V2 SHA-256: `35F644AD391DF9170BB67DB521587771B6A7D11A10BEC57043ADB130A4979E38`
- V1 rollback copy remains at `D:\\Codex\\02_正式交付区\\V09011153_A股T1四轮定时任务冻结总规则.md`.
- V1 SHA-256: `1E741462B3D999223B2A666C50692592FA82AD7033D717D29A875781C3A8BF11`

## Automation cutover

The following automations are ACTIVE and were read back from their TOML files after update:

- `a-t-1-09-45`: weekdays 09:45.
- `a-t-1-11-30`: weekdays 11:30.
- `a-t-1-13-45`: weekdays 13:45.
- `a-t-1-14-40`: weekdays 14:40.

For all four, the verified configuration is model `gpt-5.6-terra`, reasoning effort `medium`, local projectless execution, and an exact V2 rule-path reference with no V1 prompt reference.

## Workspace preservation

Pre-existing modified and untracked user files were left in place. No reset, checkout, cleanup, or destructive workspace operation was performed.

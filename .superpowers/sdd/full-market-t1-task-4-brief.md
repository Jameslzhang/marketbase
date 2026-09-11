# Task 4 Brief: Mutually exclusive T+1 decisions and only-choose-one

## Repository and allowed files

- Repository: `D:\Environment\marketbase`
- Starting commit: `2dae9971708f3f89f850b54d82095715f9d07a4c`
- Modify only `strategies/full_market_t1.py` and `tests/test_full_market_t1.py`.
- Preserve all pre-existing dirty user changes. Selectively stage only Task 4 hunks.
- Read the Task 4 section of `docs/superpowers/plans/2026-09-03-full-market-t1-execution-pipeline.md` and the decision contract in `docs/superpowers/specs/2026-09-03-full-market-t1-execution-pipeline-design.md` before coding.

## Required interfaces

- `evaluate_candidate(candidate: Mapping, objective: CandidateObjectiveData, market: Mapping) -> dict[str, object]`
- `build_full_market_decision(candidate_union: Mapping, handoff: Mapping, *, decision_at: datetime) -> dict[str, object]`
- `choose_one(rows: Sequence[Mapping]) -> str | None`

## Exact production hard gates

`buyable=true` only if every gate passes:

1. `candidate_data_status == "ready"`.
2. `price_band == "production"`.
3. `opportunity_score >= 65` (do not lower).
4. `execution_score >= 68` (do not lower).
5. `minute_evidence["confirmed"] is True`.
6. `buy_low <= price <= buy_high`.
7. `price <= no_chase_price` (accept an existing `chase_line` as the source alias but emit one canonical gate/result).
8. Candidate industry evidence says `industry_sync is True`.
9. Market `advance_ratio >= 0.35`.
10. `protection_constructible is True`.
11. `is_untradable is False`.
12. `fee_adjusted_rr >= 1.50`.

Use Task 3 `compute_execution_score`; do not copy the opportunity score into execution score. The objective/executability mapping must carry or allow construction of the six execution evidence inputs. Missing objective evidence must fail closed as `data_insufficient`, not raise for normal candidate evaluation.

## Decision classes and reasons

- All gates pass: `executable_candidate`, production group, `production_buyable=true`, `buyable=true`, `only_choose_one_eligible=true`.
- Required candidate data missing: `data_insufficient`, with explicit data reason codes.
- Production candidate with opportunity score >=55 whose failures are confirmable/non-terminal (for example minute confirmation pending, not yet in buy zone, execution score below threshold, industry sync pending): `conditional_watch`, never buyable.
- Otherwise production: `reject`.
- `shadow_40_50`: `shadow_watch` if opportunity >=55, else `shadow_reject`; always all three buyability/eligibility flags false regardless of strength.
- `excluded`: reject and never buyable.
- Produce explicit deterministic `reason_codes` for every failed gate; do not silently discard failures.

## Aggregation and ranking

- `choose_one` considers only rows where production `buyable` and `only_choose_one_eligible` are true.
- Sort descending by `(execution_score, opportunity_score, fee_adjusted_rr, amount)` and return normalized code or `None`.
- `build_full_market_decision` evaluates every union candidate against same-run handoff objective data, returns versioned top-level metadata and mutually exclusive groups. Cap the exposed `executable` group at three rows, but preserve every non-executable/rejected row in audit groups.
- Include `only_choose_one`, summary counts, and `global_status`. A non-critical market classification warning must not globally veto a ready candidate; only explicit market-wide critical readiness failure may set `global_status="data_not_ready"`.
- Accept a deterministic handoff fixture shape defined in tests; keep parsing small and explicit so Task 5 can validate/load it.

## TDD and verification

Write RED tests first for:

- both scores plus all gates produce one executable candidate;
- each individual hard gate vetoes buyable and emits its reason;
- missing data produces `data_insufficient` without exception;
- shadow never becomes production-buyable or selected;
- tie-breaking order is exact and deterministic;
- decision groups are mutually exclusive and executable is capped at three without dropping rejected/audit rows.

Run: `.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q`

Expected: zero failures. Commit only the two allowed files with message `feat: decide full-market T1 candidates`. Write report to `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-4-report.md` with RED/GREEN, commit, staged scope, and concerns.

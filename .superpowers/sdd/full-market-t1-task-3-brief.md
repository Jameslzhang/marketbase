# Task 3 Brief: Candidate readiness, completed-minute evidence, execution score

## Repository and scope

- Repository: `D:\Environment\marketbase`
- Starting commit: `e46d30d8db9fb53bd1692b7655e40174d1b511d1`
- Modify only: `strategies/full_market_t1.py`, `tests/test_full_market_t1.py`
- Work directly in the authorized dirty `main` workspace.
- Preserve every pre-existing user/worktree change. Selectively stage only Task 3 hunks.

## Required interfaces

Implement in `strategies/full_market_t1.py`:

1. Immutable `CandidateObjectiveData` dataclass with mapping fields named exactly:
   `snapshot`, `daily`, `industry`, `minute`, `minute_evidence`, `executability`.
2. `build_minute_evidence(minutes: pd.DataFrame, code: str, observed_at: datetime) -> dict[str, object]`.
3. `candidate_data_status(snapshot: Mapping, daily: Mapping, industry: Mapping | None, minute: Mapping | None) -> tuple[str, list[str]]`.
4. `compute_execution_score(evidence: Mapping[str, float]) -> float`.

## Frozen semantics

- Normalize candidate codes to six characters.
- Readiness is candidate-level. A global classification warning does not veto a candidate whose required evidence is complete.
- Missing snapshot, daily, industry, minute, or VWAP evidence is `data_insufficient`; return explicit reason codes such as `snapshot_missing`, `daily_missing`, `industry_missing`, `minute_missing`, `vwap_missing`. Do not estimate missing values.
- Completed-minute rule: exclude the currently unfinished minute according to `observed_at`. Use the last six completed rows for the candidate; last three are the hold window and preceding three the activity baseline.
- Confirmation requires all last three completed closes above both full-day VWAP and a named pivot, plus `last3_volume >= previous3_volume * 0.85`.
- Compute full-day VWAP from cumulative amount/volume across completed rows. Compute afternoon VWAP from completed rows at or after 13:00. If fewer than six completed rows exist, `confirmed=false` and include `insufficient_completed_minutes`.
- Return evidence sufficient for later gates, including `hold_minutes`, `activity_non_contracting`, `confirmed`, `last_completed_minute`, `vwap`, `afternoon_vwap`, pivot, and reason codes. Do not let a single snapshot satisfy continuity.

Execution score formula, exact and versioned as `execution_rule_version="1.0.0"`:

```python
score = 50.0
score += clamp(dist_vwap_pct * 8.0, -18.0, 18.0)
score += clamp(change_pct * 1.8, -16.0, 16.0)
score += clamp(turnover_rate * 1.2, 0.0, 10.0)
score += clamp(from_low_pct * 1.5, 0.0, 10.0)
score += clamp(dist_high_pct * 3.0, -14.0, 0.0)
if amplitude_pct > 8.0 and dist_high_pct < -3.0:
    score -= 8.0
return round(clamp(score, 0.0, 100.0), 2)
```

Missing score inputs must not be silently invented; raise a clear error or otherwise fail closed in a way covered by tests. Score alone never sets `buyable`.

## TDD and required tests

Write failing tests first for:

- complete candidate -> `ready`, no reasons;
- each required missing input, including VWAP -> `data_insufficient` with exact reason;
- seven-minute fixture confirms when the last three completed closes exceed VWAP and pivot and volume ratio is at least 0.85;
- current unfinished minute is excluded (13:45:30 means last completed minute is 13:44);
- fewer than six completed rows cannot confirm;
- exact execution-score arithmetic, clamp boundaries, high-amplitude penalty, and missing input fail-closed behavior;
- dataclass immutability.

Run:

`.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py -q`

All selected tests must pass. Verify the exact committed content does not depend on dirty-worktree-only helpers.

## Commit and report

- Commit only Task 3 hunks with message `feat: score dynamic T1 execution evidence`.
- Write `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-3-report.md` with RED/GREEN evidence, commit hash, changed files, selective-staging evidence, and concerns.

# Versioned Entry Plan Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an auditable 11:30 entry plan, review it at 13:45 with post-publication minute evidence, and render the same plan data in saved reports and chat output.

**Architecture:** Add a focused `marketbase.plan_lifecycle` module for pure plan creation and review. `scheduled_pipeline` owns artifact discovery/persistence and enriches `decision.json` before regenerating `decision_结果正文.md`; `local_workflow.format_decision_result` renders only the enriched decision, so the attachment and printed body share one source.

**Tech Stack:** Python 3.10+, pandas/pyarrow, pytest, existing JSON scheduled-run artifacts.

**Spec:** User requirements in the 2026-09-16 task and `D:/Codex/02_正式交付区/V09101022_A股T1策略与定时总规则第8版.md` section 18.

## Global Constraints

- Do not change production thresholds: opportunity 65, execution 68, freshness 120 seconds.
- Do not call a research reference an executable buy signal.
- Only `buyable=true`, fresh, in-zone, execution score at least 68, and no blocking gates may render `当前可以买`.
- Preserve the first 11:30 plan; later rounds add reviews or versions and never overwrite it.
- Missing confirmation evidence remains explicit and makes `plan_status=incomplete`; no synthetic confirmation price.
- Preserve unrelated dirty-worktree changes and do not commit automatically.

---

### Task 1: Pure frozen-plan contract

**Files:**
- Create: `marketbase/plan_lifecycle.py`
- Test: `tests/test_plan_lifecycle.py`

**Interfaces:**
- Produces: `freeze_plan_bundle(decision: Mapping, *, slot: str, published_at: datetime, source_path: Path) -> dict`
- Produces: `is_currently_buyable(plan: Mapping, row: Mapping, decision: Mapping, *, decision_at: datetime) -> bool`

- [ ] Write failing tests proving required fields are frozen, price above the buy zone renders `等待回踩`, and stale/out-of-zone/sub-68/non-buyable rows never render `当前可以买`.
- [ ] Run `pytest tests/test_plan_lifecycle.py -q` and verify failures are caused by the missing module/API.
- [ ] Implement the smallest plan builder using only existing decision fields; set `plan_status=incomplete` when confirmation price or another required price is absent.
- [ ] Re-run `pytest tests/test_plan_lifecycle.py -q` and verify green.

### Task 2: Post-publication 13:45 review

**Files:**
- Modify: `marketbase/plan_lifecycle.py`
- Test: `tests/test_plan_lifecycle.py`

**Interfaces:**
- Consumes: frozen plan bundle from Task 1 and current run-local minute rows.
- Produces: `review_frozen_plans(bundle: Mapping, decision: Mapping, minutes: pandas.DataFrame, *, reviewed_at: datetime) -> list[dict]`

- [ ] Write failing tests for `未触及`, `触及未确认`, `当前可执行`, `已经错过`, `已失效`, and a removed-from-top-three plan that remains in the review.
- [ ] Run the focused tests and confirm expected red failures.
- [ ] Implement timestamp filtering from `first_published_at`, zone overlap checks using minute high/low, protection/no-chase checks, and current executable gating via Task 1.
- [ ] Re-run focused tests and verify green.

### Task 3: Scheduled artifact persistence and prior-run discovery

**Files:**
- Modify: `marketbase/scheduled_pipeline.py`
- Test: `tests/test_scheduled_pipeline.py`

**Interfaces:**
- Produces per run: `plan.json`, enriched `decision.json`, regenerated `decision_结果正文.md`, `result.md`, and `result.json.plan_status/plan_path`.
- Consumes only the same-trade-date `1130_*` run's `plan.json`, never a `latest` alias.

- [ ] Write failing integration tests for 11:30 artifact creation and 13:45 exact prior-run loading/review.
- [ ] Run `pytest tests/test_scheduled_pipeline.py -q` and verify red.
- [ ] Add explicit `slot` flow, same-date prior-plan discovery, run-local parquet loading, decision enrichment, and atomic persistence.
- [ ] Re-run scheduled pipeline tests and verify green.

### Task 4: One-source report/chat rendering

**Files:**
- Modify: `local_workflow.py`
- Test: `tests/test_local_workflow.py`

**Interfaces:**
- Consumes: `decision.plan_bundle` and `decision.prior_plan_reviews` created by Tasks 1-3.
- Produces: complete Chinese price table and cross-round review text used identically by attachment and stdout.

- [ ] Write failing tests asserting plan ID/time, buy zone, confirmation, no-chase, protection, two target zones, validity, invalidation, `等待回踩`, and removed-plan reasons appear in the body.
- [ ] Run focused renderer tests and verify red.
- [ ] Extend the renderer without recomputing prices; read only enriched decision fields.
- [ ] Re-run renderer tests and verify green.

### Task 5: Regression and realistic replay

**Files:**
- Verify only; no new files unless a failing regression requires a scoped fix.

- [ ] Run `pytest tests/test_plan_lifecycle.py tests/test_scheduled_pipeline.py tests/test_local_workflow.py tests/test_full_market_t1.py -q`.
- [ ] Run the full test suite with `pytest -q`.
- [ ] Replay saved 2026-09-16 11:30 and 13:45 decisions through the new pure lifecycle functions, verifying 生益科技 remains visible with a concrete status and that stale/sub-68 candidates are never labeled `当前可以买`.
- [ ] Inspect `git diff --check` and the scoped diff; report any unrelated pre-existing dirty changes separately.

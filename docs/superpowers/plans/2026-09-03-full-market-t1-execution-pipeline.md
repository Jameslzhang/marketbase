# Full-Market T+1 Execution Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the production path from a complete-market dynamic scan to audited opportunity/execution scores, minute confirmation, `buyable`, and a non-trading 40–49.99 yuan shadow cohort.

**Architecture:** Keep objective collection, fast candidate generation, and lifecycle decisions as separate units. Add a focused `strategies/full_market_t1.py` adapter that consumes a versioned candidate union and the same-run objective handoff, reuses the existing lifecycle channel/dual-axis vocabulary, applies explicit execution gates, and emits one atomic decision artifact. Fix minute quality at its source by auditing the run-local minute parquet instead of the one-row shared-cache append.

**Tech Stack:** Python 3.11, pandas, pyarrow, pytest, existing `marketbase` pipeline and `strategies.strategy_lifecycle` modules.

## Global Constraints

- Production visible candidates remain Shanghai/Shenzhen main-board stocks priced at least CNY 50.00.
- Stocks priced CNY 40.00–49.99 are shadow-only and always have `production_buyable=false`, `buyable=false`, and `only_choose_one_eligible=false`.
- Do not lower the frozen opportunity or execution thresholds: opportunity 65 for production eligibility and execution 68 for execution eligibility; opportunity 55–64 remains observation-only.
- A single quote snapshot never satisfies a continuous-minute gate.
- Missing candidate industry, daily, minute, VWAP, or executability evidence yields candidate-level `data_insufficient`; do not estimate missing values.
- Only market-wide critical failures produce global `data_not_ready`; non-critical global classification gaps remain warnings and are enforced per candidate.
- Preserve all pre-existing uncommitted work. Stage and commit only files named by the current task.
- Do not update automations until code, replay, and the new frozen-rule file pass verification.

---

### Task 1: Correct run-local minute continuity auditing

**Files:**
- Modify: `marketbase/pipeline/steps.py:364`
- Modify: `marketbase/pipeline/quality.py:16`
- Test: `tests/test_local_workflow.py`

**Interfaces:**
- Consumes: `_run_minute_snapshot(..., intraday_minutes_path, intraday_minutes_audit, ...)`
- Produces: `minute_audit["sequence_audit"]` based on the run-local parquet and `minute_audit["collection_audit"]` preserving collector totals.

- [ ] **Step 1: Write the failing regression test**

Add a test that creates a run-local parquet with three distinct completed minutes for two codes, while the shared cache contains one appended snapshot minute:

```python
def test_minute_audit_prefers_run_local_history_over_shared_snapshot(tmp_path, monkeypatch):
    run_path = tmp_path / "run" / "intraday_minutes.parquet"
    run_path.parent.mkdir()
    pd.DataFrame([
        {"code": code, "timestamp": f"2026-09-03 13:4{minute}:00", "close": price, "volume": 100, "amount": price * 100}
        for minute, price in [(0, 10.0), (1, 10.1), (2, 10.2)]
        for code in ["000001", "600000"]
    ]).to_parquet(run_path)
    audit = _run_minute_snapshot(
        frame=_snapshot_frame(), cache_root=tmp_path / "cache",
        observed_at=datetime(2026, 9, 3, 13, 43, tzinfo=CN_TZ),
        all_codes=["000001", "600000"], intraday_minutes_path=run_path,
        intraday_minutes_audit={"status": "collected", "actual_minutes": 3, "codes_with_data": 2},
        emit=lambda _message: None,
    )
    assert audit["sequence_audit"]["actual_minutes"] == 3
    assert audit["collection_audit"]["actual_minutes"] == 3
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py::test_minute_audit_prefers_run_local_history_over_shared_snapshot -q`

Expected: FAIL because the current implementation audits `cache/intraday_1m.parquet`, which contains only the appended snapshot minute.

- [ ] **Step 3: Implement run-local audit selection**

In `_run_minute_snapshot`, retain the shared-cache append for history, but normalize and audit `run_ohlcv_path` when it exists:

```python
minute_audit["collection_audit"] = dict(intraday_minutes_audit or {})
sequence_source = run_ohlcv_path if run_ohlcv_path and run_ohlcv_path.exists() else intraday_path
seq = build_intraday_sequence(sequence_source)
```

Ensure `build_intraday_sequence` accepts the run-local schema (`timestamp`, `close`) through its existing normalization path. Do not fall back to prior-day data.

- [ ] **Step 4: Make quality use dynamic expected minutes**

Update `_compute_minute_quality` to prefer `expected_minutes_dynamic` and fall back to `total_expected_minutes`:

```python
expected = int(seq_audit.get("expected_minutes_dynamic") or seq_audit.get("total_expected_minutes", 240))
minimum_full = max(int(expected * 0.83), min(200, expected))
if actual >= minimum_full and missing <= 40 and breaks <= 3:
    return "full"
```

- [ ] **Step 5: Verify GREEN and regression coverage**

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py -q`

Expected: all `tests/test_local_workflow.py` tests pass.

- [ ] **Step 6: Commit only Task 1 files**

```powershell
git add -- marketbase/pipeline/steps.py marketbase/pipeline/quality.py tests/test_local_workflow.py
git commit -m "fix: audit run-local intraday minutes"
```

---

### Task 2: Produce a versioned dynamic candidate union with price bands

**Files:**
- Create: `strategies/full_market_t1.py`
- Modify: `fast_t1_scan.py`
- Create: `tests/test_full_market_t1.py`
- Modify: `tests/test_fast_t1_scan.py`

**Interfaces:**
- Produces: `classify_price_band(code: str, market: str, price: float) -> str`
- Produces: `build_candidate_union(frame: pd.DataFrame, *, trade_date: str, observed_at: str, market_rows: int, funnel: dict[str, int]) -> dict[str, object]`
- Produces: `write_candidate_union(payload: dict[str, object], path: Path) -> Path`
- Fast scan writes: `data/cache/fast/candidate_union_YYYYMMDD_HHMM.json`.

- [ ] **Step 1: Write failing price-band tests**

```python
@pytest.mark.parametrize(("price", "expected"), [
    (39.99, "excluded"),
    (40.00, "shadow_40_50"),
    (49.99, "shadow_40_50"),
    (50.00, "production"),
])
def test_classify_price_band_boundaries(price, expected):
    assert classify_price_band("600000", "sh", price) == expected

def test_non_main_board_is_excluded_at_any_price():
    assert classify_price_band("300001", "sz", 80.0) == "excluded"
    assert classify_price_band("688001", "sh", 80.0) == "excluded"
    assert classify_price_band("920001", "bj", 80.0) == "excluded"
```

- [ ] **Step 2: Run the boundary tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`

Expected: import failure because `strategies.full_market_t1` does not exist.

- [ ] **Step 3: Implement the minimal candidate-union module**

Use these constants and boundary function:

```python
PRODUCTION_MIN_PRICE = 50.0
SHADOW_MIN_PRICE = 40.0
RESTRICTED_PREFIXES = ("300", "301", "688")

def classify_price_band(code: str, market: str, price: float) -> str:
    normalized = str(code).zfill(6)
    if market == "bj" or normalized.startswith(RESTRICTED_PREFIXES):
        return "excluded"
    if price >= PRODUCTION_MIN_PRICE:
        return "production"
    if price >= SHADOW_MIN_PRICE:
        return "shadow_40_50"
    return "excluded"
```

`build_candidate_union` must copy all available scan evidence, add `candidate_reason`, `price_band`, `production_buyable=false`, and include top-level `schema_version="1.0.0"`, `trade_date`, `observed_at`, `market_rows`, and `funnel`.

- [ ] **Step 4: Add atomic JSON writing and fast-scan integration**

Implement `write_candidate_union` with a sibling `.tmp` file and `os.replace`. Call it after `out_df` is built in `fast_t1_scan.main`. Use the exact same `observed_at`, market-row count, and funnel values as the CSV report.

- [ ] **Step 5: Verify candidate-union tests and existing fast-scan tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit only Task 2 files**

```powershell
git add -- strategies/full_market_t1.py fast_t1_scan.py tests/test_full_market_t1.py tests/test_fast_t1_scan.py
git commit -m "feat: emit dynamic T1 candidate union"
```

---

### Task 3: Add candidate-level data readiness and execution scoring

**Files:**
- Modify: `strategies/full_market_t1.py`
- Modify: `tests/test_full_market_t1.py`

**Interfaces:**
- Produces: `CandidateObjectiveData`, an immutable dataclass containing `snapshot`, `daily`, `industry`, `minute`, `minute_evidence`, and `executability` mappings.
- Produces: `build_minute_evidence(minutes: pd.DataFrame, code: str, observed_at: datetime) -> dict[str, object]`
- Produces: `candidate_data_status(snapshot: Mapping, daily: Mapping, industry: Mapping | None, minute: Mapping | None) -> tuple[str, list[str]]`
- Produces: `compute_execution_score(evidence: Mapping[str, float]) -> float`

- [ ] **Step 1: Write failing candidate-quality tests**

```python
def test_complete_candidate_is_not_vetoed_by_global_classification_warning():
    status, reasons = candidate_data_status(SNAPSHOT, DAILY, INDUSTRY, MINUTE)
    assert status == "ready"
    assert reasons == []

@pytest.mark.parametrize(("missing", "reason"), [
    ("industry", "industry_missing"),
    ("daily", "daily_missing"),
    ("minute", "minute_missing"),
    ("vwap", "vwap_missing"),
])
def test_candidate_missing_required_evidence_is_data_insufficient(missing, reason):
    inputs = complete_candidate_inputs()
    inputs[missing] = None
    status, reasons = candidate_data_status(**inputs)
    assert status == "data_insufficient"
    assert reason in reasons
```

- [ ] **Step 2: Write failing completed-minute confirmation tests**

Use seven minute rows so the last three completed bars can be compared with the prior three:

```python
def test_three_completed_minutes_above_vwap_and_pivot_confirm():
    evidence = build_minute_evidence(_seven_minute_fixture(last_closes=[10.21, 10.23, 10.24]), "600000", OBSERVED_AT)
    assert evidence["hold_minutes"] == 3
    assert evidence["activity_non_contracting"] is True
    assert evidence["confirmed"] is True

def test_current_unfinished_minute_is_excluded():
    evidence = build_minute_evidence(_fixture_with_134500_spike(), "600000", datetime(2026, 9, 3, 13, 45, 30, tzinfo=CN_TZ))
    assert evidence["last_completed_minute"] == "13:44"
    assert evidence["confirmed"] is False
```

The confirmation rule is exact: the last three completed minute closes must be above both full-day VWAP and named pivot; their total volume must be at least 85% of the preceding three completed minutes.

- [ ] **Step 3: Run quality/minute tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`

Expected: FAIL because the three interfaces are not implemented.

- [ ] **Step 4: Implement candidate readiness and minute evidence**

Add the immutable `CandidateObjectiveData` dataclass with the six mapping fields named above. Normalize codes to six-character strings. Compute full-day VWAP from cumulative amount/volume, afternoon VWAP from rows at or after 13:00, and `activity_non_contracting = last3_volume >= previous3_volume * 0.85`. If fewer than six completed rows exist, set `confirmed=false` and add `insufficient_completed_minutes`.

- [ ] **Step 5: Implement the frozen execution-score formula**

Use the already validated quant-sandbox formula as the initial `execution_rule_version="1.0.0"`:

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

The score is necessary but not sufficient: continuous-minute confirmation, buy zone, no-chase, industry, market veto, protection, executability, and price-band gates retain veto power.

- [ ] **Step 6: Verify GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`

Expected: all tests pass.

```powershell
git add -- strategies/full_market_t1.py tests/test_full_market_t1.py
git commit -m "feat: score dynamic T1 execution evidence"
```

---

### Task 4: Build mutually exclusive production, watch, reject, and shadow decisions

**Files:**
- Modify: `strategies/full_market_t1.py`
- Modify: `tests/test_full_market_t1.py`

**Interfaces:**
- Produces: `evaluate_candidate(candidate: Mapping, objective: CandidateObjectiveData, market: Mapping) -> dict[str, object]`
- Produces: `build_full_market_decision(candidate_union: Mapping, handoff: Mapping, *, decision_at: datetime) -> dict[str, object]`
- Produces: `choose_one(rows: Sequence[Mapping]) -> str | None`.

- [ ] **Step 1: Write failing decision-contract tests**

```python
def test_buyable_requires_both_scores_and_all_hard_gates():
    row = evaluate_candidate(candidate(opportunity_score=70, price_band="production"), ready_objective(), normal_market())
    assert row["execution_score"] >= 68
    assert row["buyable"] is True
    assert row["decision"] == "executable_candidate"

def test_single_failed_hard_gate_vetoes_buyable():
    objective = ready_objective(minute_confirmed=False)
    row = evaluate_candidate(candidate(opportunity_score=70, price_band="production"), objective, normal_market())
    assert row["buyable"] is False
    assert row["decision"] == "conditional_watch"
    assert "minute_confirmation_pending" in row["reason_codes"]

def test_shadow_candidate_can_never_be_selected():
    shadow = evaluate_candidate(candidate(opportunity_score=90, price_band="shadow_40_50"), strongest_objective(), normal_market())
    assert shadow["production_buyable"] is False
    assert shadow["buyable"] is False
    assert choose_one([shadow]) is None
```

- [ ] **Step 2: Run decision tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`

Expected: FAIL because decision aggregation is absent.

- [ ] **Step 3: Implement exact gates**

`buyable=true` requires every condition below:

```python
hard_gates = {
    "candidate_data_ready": candidate_data_status == "ready",
    "production_price_band": price_band == "production",
    "opportunity_score": opportunity_score >= 65,
    "execution_score": execution_score >= 68,
    "minute_confirmation": minute_evidence["confirmed"] is True,
    "inside_buy_zone": buy_low <= price <= buy_high,
    "below_no_chase": price <= no_chase_price,
    "industry_sync": industry_sync is True,
    "market_not_vetoed": advance_ratio >= 0.35,
    "protection_constructible": protection_constructible is True,
    "normal_executability": is_untradable is False,
    "fee_adjusted_economics": fee_adjusted_rr >= 1.50,
}
```

Decision mapping:

- all gates pass: `executable_candidate`;
- data missing: `data_insufficient`;
- opportunity at least 55 and the failure is confirmable rather than terminal: `conditional_watch`;
- otherwise: `reject`;
- any shadow price band: `shadow_watch` or `shadow_reject`, never a production group.

- [ ] **Step 4: Implement ranking and `only_choose_one`**

Rank production executable rows by `(execution_score, opportunity_score, fee_adjusted_rr, amount)` descending. Return the first code or `None`. Cap report groups at three executable candidates and preserve all rejected rows in the audit payload.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py -q`

Expected: all tests pass.

```powershell
git add -- strategies/full_market_t1.py tests/test_full_market_t1.py
git commit -m "feat: decide full-market T1 candidates"
```

---

### Task 5: Add an atomic production orchestration command

**Files:**
- Modify: `local_workflow.py`
- Modify: `strategies/full_market_t1.py`
- Modify: `tests/test_local_workflow.py`
- Modify: `tests/test_full_market_t1.py`

**Interfaces:**
- Adds CLI flags: `local_workflow.py full-market-t1 --candidate-union PATH --decision-at ISO_8601 --output PATH`
- Produces: a versioned decision JSON and appends a shadow audit JSONL record only after successful validation.

- [ ] **Step 1: Write failing CLI test**

```python
def test_full_market_t1_cli_writes_atomic_decision(tmp_path, monkeypatch):
    output = tmp_path / "decision.json"
    rc = main([
        "--data-root", str(tmp_path / "daily_runs"),
        "full-market-t1",
        "--candidate-union", str(FIXTURE_UNION),
        "--decision-at", "2026-09-03T13:45:00+08:00",
        "--output", str(output),
    ])
    assert rc == 0
    assert json.loads(output.read_text(encoding="utf-8"))["only_choose_one"] is None
    assert not output.with_suffix(".tmp").exists()
```

- [ ] **Step 2: Run the CLI test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_workflow.py::test_full_market_t1_cli_writes_atomic_decision -q`

Expected: argparse rejects the unknown `full-market-t1` command.

- [ ] **Step 3: Implement CLI parsing and orchestration**

Add arguments exactly as tested. Validate that candidate union and handoff have the same trading date and that their observation times are no more than 20 minutes apart. Load only paths declared by `latest_codex_input.json`. Write the decision to `<output>.tmp`, flush and close it, then call `os.replace`.

- [ ] **Step 4: Implement shadow ledger append**

Write one JSON object per shadow candidate to `data/daily_runs/shadow/full_market_t1_shadow.jsonl`, including trade date, decision time, code, evidence, score versions, decision, and `production_buyable=false`. Do not write the ledger if the decision artifact fails validation.

- [ ] **Step 5: Verify CLI, lifecycle, and local-workflow suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit only Task 5 files**

```powershell
git add -- local_workflow.py strategies/full_market_t1.py tests/test_local_workflow.py tests/test_full_market_t1.py
git commit -m "feat: orchestrate full-market T1 decisions"
```

---

### Task 6: Replay 2026-09-03 and verify the repaired semantics

**Files:**
- Create: `tests/fixtures/full_market_t1/2026-09-03/README.md`
- Create: `tests/fixtures/full_market_t1/2026-09-03/expected_summary.json`
- Modify: `tests/test_full_market_t1.py`

**Interfaces:**
- Consumes the saved run `data/daily_runs/2026-09-03/134656_intraday_1300_objective_data` and `data/cache/fast/scan_result_20260903_1353.csv`.
- Produces a deterministic replay assertion without copying large market datasets into Git.

- [ ] **Step 1: Write a failing replay test**

```python
def test_20260903_replay_is_audited_empty_not_global_data_not_ready(saved_20260903_inputs):
    decision = replay_full_market_decision(**saved_20260903_inputs)
    assert decision["global_status"] == "decision_ready"
    assert decision["only_choose_one"] is None
    assert decision["summary"]["executable"] == 0
    assert decision["summary"]["shadow_count"] >= 1
    assert all(row["reason_codes"] for row in decision["rejected"])
```

- [ ] **Step 2: Run the replay test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py::test_20260903_replay_is_audited_empty_not_global_data_not_ready -q`

Expected: FAIL until the replay loader and non-critical quality-warning handling are connected.

- [ ] **Step 3: Add the deterministic fixture manifest and replay adapter**

The fixture README records source hashes and expected paths. `expected_summary.json` contains:

```json
{
  "trade_date": "2026-09-03",
  "market_rows": 5546,
  "only_choose_one": null,
  "executable": 0,
  "global_status": "decision_ready"
}
```

Treat `classification_coverage_insufficient` as a market warning when each evaluated candidate has valid industry evidence. Do not suppress any candidate-specific missing-data reason.

- [ ] **Step 4: Run replay and full test suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: zero failed tests.

- [ ] **Step 5: Commit replay coverage**

```powershell
git add -- tests/fixtures/full_market_t1/2026-09-03 tests/test_full_market_t1.py
git commit -m "test: replay full-market T1 decision"
```

---

### Task 7: Publish frozen rule V2 and switch the four automations

**Files:**
- Create: `docs/strategy/a-share-t1-four-run-frozen-rules-v2.md`
- Publish at release time into: `D:\Codex\02_正式交付区` using the exact runtime-generated filename from Step 3.
- Preserve: `D:\Codex\02_正式交付区\V09011153_A股T1四轮定时任务冻结总规则.md`

**Interfaces:**
- New rule names the `full-market-t1` command, candidate-level quality contract, minute-audit source, and 40–49.99 shadow boundary.
- All four automations reference the same newly published absolute path.

- [ ] **Step 1: Write the repository rule document**

Copy the old frozen rule into the repository document and change only these governed sections:

- §3: add the `full-market-t1` command and versioned decision artifact;
- §3.4: distinguish global `data_not_ready` from candidate `data_insufficient`;
- §4.2: keep production minimum CNY 50 and add CNY 40–49.99 shadow-only rules;
- §6: define `execution_rule_version=1.0.0` and require independent execution score;
- §12: make the 11:30, 13:45, and 14:40 duties consume the new decision artifact;
- §13: require at least three trading days and 20 scored samples per channel before shadow promotion.

- [ ] **Step 2: Verify the rule diff contains no unrelated strategy changes**

Run: `git diff --no-index --word-diff=plain "D:\Codex\02_正式交付区\V09011153_A股T1四轮定时任务冻结总规则.md" docs/strategy/a-share-t1-four-run-frozen-rules-v2.md`

Expected: differences are confined to the six governed changes listed above.

- [ ] **Step 3: Publish with the required China-time filename**

Use PowerShell to obtain the timestamp, then copy the verified repository rule to the formal delivery area:

```powershell
$stamp = Get-Date -Format 'MMddHHmm'
$delivery = "D:\Codex\02_正式交付区\V${stamp}_A股T1四轮定时任务冻结总规则第2版.md"
Copy-Item -LiteralPath 'docs\strategy\a-share-t1-four-run-frozen-rules-v2.md' -Destination $delivery
Get-Item -LiteralPath $delivery
```

- [ ] **Step 4: Commit the repository rule**

```powershell
git add -- docs/strategy/a-share-t1-four-run-frozen-rules-v2.md
git commit -m "docs: freeze full-market T1 rules v2"
```

- [ ] **Step 5: Update all four automation prompts through the app API**

List the four automations, preserve their schedules, model, reasoning effort, notification policy, status, and task-specific responsibility, and replace only the old frozen-rule path with the verified V2 delivery path. Do not create duplicate automations.

- [ ] **Step 6: Verify release and rollback path**

Run the target suites once more:

` .venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q`

Expected: zero failed tests. Then view each automation and confirm all four reference the same V2 path. Confirm the old V1 file still exists and record its path as the rollback target.

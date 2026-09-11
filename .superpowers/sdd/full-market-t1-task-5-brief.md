# Task 5 Brief: Atomic full-market T1 production command

## Repository and allowed files

- Repository: `D:\Environment\marketbase`
- Starting commit: `d09c420`
- Modify only `local_workflow.py`, `strategies/full_market_t1.py`, `tests/test_local_workflow.py`, `tests/test_full_market_t1.py`.
- Preserve all existing dirty user changes and selectively stage only Task 5 hunks.
- Read Task 5 in the implementation plan and §§4.2, 4.3, 5, 6, 7 of the design before coding.

## CLI contract

Add exactly:

```text
local_workflow.py full-market-t1 \
  --candidate-union PATH \
  --decision-at ISO_8601 \
  --output PATH
```

Use the normal top-level `--data-root`; its `latest_codex_input.json` is the only objective-data handoff manifest. Return 0 on success and nonzero on contract/I/O failure via the existing command boundary.

## Input validation and trust boundary

- Candidate union is the explicitly supplied CLI path.
- Read objective files only from exact path keys declared by `<data-root>/latest_codex_input.json`; never discover, glob, or fall back to shared caches/run directories.
- Required declared inputs for an evaluable run: market snapshot, daily indicators, classification map, industry aggregation or market breadth industry data, market breadth, run-local intraday minutes, and data audit. Missing declaration/file is a clear contract error.
- Candidate union `trade_date` and handoff observation date must match. Use handoff `generated_at` as its observation time unless an explicit same-run `observed_at` exists.
- Candidate-union `observed_at` and handoff observation time must be no more than 20 minutes apart in absolute value. Naive/unparseable times are contract errors; normalize timezone-aware ISO values.
- Validate before any decision or shadow-ledger write. Invalid same-date/window/path/schema inputs leave both output and ledger untouched.

## Objective adapter

Build one `CandidateObjectiveData` per candidate code from only the declared files:

- exact-code snapshot row;
- exact-code daily-indicator row;
- classification row joined to industry aggregate / market-breadth industry evidence;
- exact-code rows from the run-local minute parquet and `build_minute_evidence` using `decision_at`;
- explicit executability and six execution-score inputs derived only from objective/candidate fields when mathematically defined; never invent missing values.

Named pivot must be traceable to a candidate field already emitted by the scan (for example an explicit pivot or buy-zone/technical anchor); record its source. Do not let a single snapshot replace completed-minute continuity.

Industry sync must be emitted explicitly from declared industry evidence under a deterministic documented rule. Market advance ratio is `full_market.advance_count / full_market.total` when valid.

For the execution inputs, use direct fields if present; otherwise only deterministic arithmetic from objective facts is allowed: distance from VWAP, percent from low/high, and amplitude from high/low/pre-close. If a denominator is missing/zero, leave the field missing so Task 4 returns `data_insufficient`. `is_untradable` must be explicit from the objective snapshot. Protection/buy-zone/no-chase and economics must come from candidate evidence; do not synthesize permissive defaults. Preserve score/rule versions and input-path/checksum metadata in the decision output.

## Output and shadow ledger

- Call `build_full_market_decision` after validation.
- Validate the returned decision has required version, groups, `audit_rows`, summary, and `only_choose_one`, and that every shadow row has all three production eligibility flags false.
- Write JSON UTF-8 to sibling `<output>.tmp`, flush and close, then `os.replace`; no leftover tmp.
- Only after all input/decision validation succeeds, append one compact JSON object per shadow candidate to `<data-root>/shadow/full_market_t1_shadow.jsonl`.
- Each ledger record includes trade date, decision time, code, full evidence/audit row, score/rule versions, decision, and `production_buyable=false`.
- Do not append the ledger if validation or decision artifact writing fails. Repeated command runs may append another audit observation; do not overwrite prior JSONL.

## TDD coverage

Write failing tests first for:

1. CLI happy path writes atomic decision, `only_choose_one` allowed to be null, no tmp remains, and shadow ledger appends valid record.
2. Date mismatch and >20-minute mismatch both return nonzero and write neither output nor ledger.
3. A path absent from the handoff manifest is never discovered/fallback-loaded.
4. Candidate missing one objective component becomes candidate `data_insufficient`, not a global crash, when the market-wide contract remains valid.
5. Shadow ledger is not written if decision-output validation or atomic replace fails.
6. Only declared input paths are reflected in input metadata/checksums.

Run:

```text
.venv\Scripts\python.exe -m pytest tests\test_full_market_t1.py tests\test_local_workflow.py tests\test_strategy_lifecycle.py -q
```

Expected: zero failures. Commit only the four allowed files with `feat: orchestrate full-market T1 decisions`. Write report to `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-5-report.md` containing RED/GREEN, exact commit, selective staging evidence, and concerns.

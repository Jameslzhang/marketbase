# Task 7A Brief: Frozen rule V2 repository document

## Scope and sequencing

- Repository: `D:\Environment\marketbase`
- Starting commit: `a0236e0`
- Create only `docs/strategy/a-share-t1-four-run-frozen-rules-v2.md` in the repository.
- Source V1 (read-only, preserve unchanged): `D:\Codex\02_正式交付区\V09011153_A股T1四轮定时任务冻结总规则.md`.
- Do not publish the formal delivery copy and do not update automations in this subtask. The parent will do both only after review approval.
- Preserve every dirty user change; selectively stage only the new repository document.

## Editing contract

Copy the full V1 text into the repository document and change only these governed areas, retaining all unrelated strategy rules, thresholds, risk controls, wording, and section numbering:

1. §3: add the production `full-market-t1` command, candidate-union input, same-run objective handoff, and versioned decision artifact.
2. §3.4: distinguish market-wide critical `data_not_ready` from per-candidate `data_insufficient`; classification coverage insufficiency is a warning when candidate industry evidence is present.
3. §4.2: keep formal production minimum price at CNY 50.00; define CNY 40.00–49.99 as shadow-only with `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`; below 40 excluded; preserve main-board/BJ/300/301/688 restrictions.
4. §6: freeze independent `execution_rule_version=1.0.0`, retain opportunity threshold 65 and execution threshold 68, explicitly forbid substituting one score for the other, and retain all hard-gate vetoes.
5. §12: make 11:30, 13:45, and 14:40 duties consume the new versioned decision artifact. 11:30 with insufficient continuous minutes may only output conditional observation; 13:45/14:40 run complete execution evaluation. 09:45 remains historical replay/audit only.
6. §13: require at least 3 trading days and at least 20 scored/executable samples per channel before any shadow rule may be proposed for promotion; promotion still needs governed review and may not automatically change production thresholds.

Also record the corrected minute-audit source: current run-local `intraday_minutes.parquet` with dynamic expected minutes, while shared cache is history only. This belongs in the existing data/quality governed subsection, not a new unrelated policy.

No broker connection, automatic order, automatic parameter change, or relaxation of existing controls may be added.

## Verification and commit

- Compare V1 and V2 with a word diff and manually confirm differences are confined to the six areas above plus the minute-audit clarification.
- Verify V1 still exists and is byte-unchanged.
- Ensure V2 explicitly contains: `full-market-t1`, `data_not_ready`, `data_insufficient`, `intraday_minutes.parquet`, `40.00`, `49.99`, `50.00`, opportunity `65`, execution `68`, `execution_rule_version=1.0.0`, `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`, `3` trading days, and `20` samples.
- Commit only the new repo file with message `docs: freeze full-market T1 rules v2`.
- Write report to `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-7-report.md` with V1 SHA-256, V2 SHA-256, governed-diff summary, commit, and concerns. Keep the report uncommitted unless it was already tracked by task convention.

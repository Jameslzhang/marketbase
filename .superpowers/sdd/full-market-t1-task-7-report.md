# Task 7A Report: frozen rule V2 repository document

## Scope

- Repository: `D:\Environment\marketbase`
- Source V1: `D:\Codex\02_正式交付区\V09011153_A股T1四轮定时任务冻结总规则.md`
- Repository V2: `docs/strategy/a-share-t1-four-run-frozen-rules-v2.md`
- Commit created: `a06e29213285c708c468ae3d813a7ee74a8c3489`
- Formal delivery copy: not created
- Automations: not modified

## SHA-256

- V1: `1E741462B3D999223B2A666C50692592FA82AD7033D717D29A875781C3A8BF11`
- V2: `35F644AD391DF9170BB67DB521587771B6A7D11A10BEC57043ADB130A4979E38`

## Governed Diff Summary

Word diff and manual review confirmed changes are confined to the requested governance areas plus the minute-audit clarification:

- §3.1 added the production `full-market-t1` command, `--candidate-union` input contract, same-run objective handoff requirement, and versioned decision artifact contract.
- §3.3 clarified that minute audit uses current run-local `intraday_minutes.parquet` with deduplicated actual minutes and dynamic expected minutes; shared cache is history only.
- §3.4 split market-wide `data_not_ready` from per-candidate `data_insufficient`, and kept classification coverage insufficiency as warning-only when candidate industry evidence exists.
- §4.2 froze the production minimum at `50.00`, defined `40.00`-`49.99` as shadow-only with `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`, and excluded below `40.00`.
- §6 froze `execution_rule_version=1.0.0`, retained opportunity `65` and execution `68`, forbade substituting one score for the other, and preserved hard-gate vetoes.
- §12 made 11:30, 13:45, and 14:40 consume the versioned decision artifact; 11:30 remains conditional-only when continuous-minute evidence is insufficient; 09:45 remains historical replay/audit only.
- §13 required at least `3` trading days and at least `20` scored, execution-evaluated samples per channel before any shadow rule promotion proposal, with no automatic production threshold changes.

## Verification

- Verified V1 still exists at the original path.
- Recomputed V1 SHA-256 after the edit/commit flow; no byte change observed.
- Verified V2 explicitly contains: `full-market-t1`, `data_not_ready`, `data_insufficient`, `intraday_minutes.parquet`, `40.00`, `49.99`, `50.00`, opportunity `65`, execution `68`, `execution_rule_version=1.0.0`, `production_buyable=false`, `buyable=false`, `only_choose_one_eligible=false`, `3`, and `20`.
- `git diff --no-index --word-diff=plain --unified=0` between V1 and V2 showed edits only in the expected sections.

## Concerns

- Git emitted LF/CRLF normalization warnings for the new repository Markdown file during diff/add/commit. The commit succeeded and content verification passed.
- The report file is intentionally left uncommitted per task instructions.

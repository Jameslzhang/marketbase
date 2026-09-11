# Task 2 Brief: Versioned dynamic candidate union and price bands

This task is part of `docs/superpowers/plans/2026-09-03-full-market-t1-execution-pipeline.md`. Task 1 is complete at `bd00aec`.

## Global constraints

- Production visible candidates remain Shanghai/Shenzhen main-board stocks priced at least CNY 50.00.
- Stocks priced CNY 40.00–49.99 are shadow-only and always have `production_buyable=false`, `buyable=false`, and `only_choose_one_eligible=false`.
- Do not lower opportunity 65 or execution 68 thresholds.
- Dynamic candidates must originate from the complete-market scan, never fixed `watchlist.json`.
- Preserve all pre-existing uncommitted work. `fast_t1_scan.py` and `tests/test_fast_t1_scan.py` already contain unrelated unstaged changes; do not revert or commit those hunks.

## Files

- Create: `strategies/full_market_t1.py`
- Modify: `fast_t1_scan.py`
- Create: `tests/test_full_market_t1.py`
- Modify: `tests/test_fast_t1_scan.py`

## Required interfaces

```python
def classify_price_band(code: str, market: str, price: float) -> str: ...

def build_candidate_union(
    frame: pd.DataFrame,
    *,
    trade_date: str,
    observed_at: str,
    market_rows: int,
    funnel: dict[str, int],
) -> dict[str, object]: ...

def write_candidate_union(payload: dict[str, object], path: Path) -> Path: ...
```

Constants:

```python
PRODUCTION_MIN_PRICE = 50.0
SHADOW_MIN_PRICE = 40.0
RESTRICTED_PREFIXES = ("300", "301", "688")
```

`classify_price_band` rules:

```python
normalized = str(code).zfill(6)
if market == "bj" or normalized.startswith(RESTRICTED_PREFIXES):
    return "excluded"
if price >= PRODUCTION_MIN_PRICE:
    return "production"
if price >= SHADOW_MIN_PRICE:
    return "shadow_40_50"
return "excluded"
```

## Required behavior

1. Use TDD. First add boundary tests for 39.99→excluded, 40.00→shadow, 49.99→shadow, 50.00→production, and 300/301/688/BJ exclusions at high prices. Watch them fail because the module is absent.
2. `build_candidate_union` must return top-level `schema_version="1.0.0"`, `trade_date`, `observed_at`, `market_rows`, `funnel`, and `candidates`.
3. Each candidate copies all available scan evidence and adds `candidate_reason`, `price_band`, `production_buyable=false`, `buyable=false`, and `only_choose_one_eligible=false`. Task 4 will later promote eligible production rows; Task 2 must never mark rows buyable.
4. `candidate_reason` is the existing `opportunity_tags` value normalized to a list. If unavailable, use an empty list, not invented evidence.
5. `write_candidate_union` must write UTF-8 JSON atomically through a sibling `.tmp` path and `os.replace`, returning the final `Path`.
6. Modify `fast_t1_scan.main` after `out_df` is created to write `data/cache/fast/candidate_union_YYYYMMDD_HHMM.json` from the same snapshot observation time and the exact full-market counts used for the CSV/report. Log the output path.
7. Do not change fast scan filtering or opportunity scoring in this task.
8. Run `tests/test_full_market_t1.py` and `tests/test_fast_t1_scan.py` with the repository `.venv`; both must have zero failures.
9. Commit only Task 2 changes. Because two existing files are dirty, use selective staging and verify no pre-existing hunks enter the commit.

## Test examples

```python
@pytest.mark.parametrize(("price", "expected"), [
    (39.99, "excluded"),
    (40.00, "shadow_40_50"),
    (49.99, "shadow_40_50"),
    (50.00, "production"),
])
def test_classify_price_band_boundaries(price, expected):
    assert classify_price_band("600000", "sh", price) == expected

def test_candidate_union_is_never_buyable_at_generation_time():
    payload = build_candidate_union(FRAME, trade_date="2026-09-03", observed_at="2026-09-03T13:45:00+08:00", market_rows=5546, funnel={"initial": 5546})
    assert all(row["buyable"] is False for row in payload["candidates"])
    assert all(row["only_choose_one_eligible"] is False for row in payload["candidates"])
```

Add an integration-focused fast-scan test by monkeypatching `write_candidate_union` or its destination inputs so the test proves `main` passes the same trade date, observation time, market rows, and funnel used by the scan output without calling live providers.

## Report contract

Write `D:\Environment\marketbase\.superpowers\sdd\full-market-t1-task-2-report.md`. Include status, commits, changed files, exact RED and GREEN commands/results, selective-staging evidence, self-review, and concerns. Return only status, commits, one-line test summary, and concerns.

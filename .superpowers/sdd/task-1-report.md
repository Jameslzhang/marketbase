# Task 1 Report: Neutral Mathematical Indicators

## Status

DONE

## Modified files

- `marketbase/neutral_indicators.py`
- `tests/test_neutral_indicators.py`

Required report file:

- `.superpowers/sdd/task-1-report.md`

No Git commit was created.

## TDD verification

### RED

Command:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q
```

Output:

```text
ModuleNotFoundError: No module named 'marketbase.neutral_indicators'
1 error during collection
```

### Green

Command:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q
```

Output:

```text
...                                                                      [100%]
3 passed in 0.85s
```

Command:

```text
.\.venv\Scripts\python.exe -m ruff check marketbase\neutral_indicators.py tests\test_neutral_indicators.py
```

Output:

```text
All checks passed!
```

## Self-check

- Covered complete 250-row daily input, including MA250, RSI14, ATR14 and ATR14 percentage.
- Covered short history returning `None` for unavailable indicators.
- Covered zero-volume VWAP returning `None` and known VWAP calculation.
- Confirmed output keys are exactly the required neutral keys.
- Confirmed forbidden fields `signal_score`, `macd_status`, and `rsi_status` are absent.
- Dates are emitted as ISO date strings; calculation time uses the supplied timestamp or local timezone current time.
- Existing unrelated working-tree changes were left untouched.

## Concerns

- The brief does not define a separate warm-up convention for MACD and ATR. The implementation uses conventional minimum data requirements: MACD DIF requires EMA12/EMA26 warm-up, DEA/histogram require nine valid DIF values, and ATR14 requires fourteen valid true ranges.
- No full-suite run was requested; verification was limited to the specified focused pytest and Ruff commands.

## Review Fixes Appended

### Changes

- Extended `tests/test_neutral_indicators.py` with a fixed 35-row close-price fixture and explicit MACD DIF, DEA, and histogram expectations. The values cover EMA12/EMA26, DEA9, and `hist = (DIF - DEA) * 2` regressions.
- Added a default `calculated_at` assertion for a local timezone-aware ISO timestamp.
- Added an assertion that a timezone-aware input `datetime` preserves its exact value and timezone in the output.
- Added `atr14_pct is None` to the short-history assertions.
- `marketbase/neutral_indicators.py` did not require a change.

### Verification

Command:

```text
.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q
```

Complete output:

```text
......                                                                   [100%]
6 passed in 1.00s
```

Command:

```text
.\.venv\Scripts\python.exe -m ruff check marketbase\neutral_indicators.py tests\test_neutral_indicators.py
```

Complete output:

```text
All checks passed!
```

No Git commit was created.

### Concerns

- Verification remains scoped to the requested neutral-indicators test module and Ruff targets; the full test suite was not run.
- The MACD constants are intentionally explicit regression values for the fixed fixture, while the implementation remains unchanged.

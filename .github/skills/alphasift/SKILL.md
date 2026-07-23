---
name: alphasift
description: "Collect objective A-share market data and fulfill validated daily or current-day minute data requests."
---

# AlphaSift Objective Data

Use AlphaSift to retrieve source data, derived neutral indicators, and audit
evidence. It does not select, rank, recommend, or interpret securities.

## Commands

```bash
alphasift --data-root ./data collect
alphasift --data-root ./data fulfill-request
```

`collect` retrieves a current three-market snapshot, daily history, neutral
indicators, basic classifications, and data-audit evidence. It keeps caches and
checkpoints under the data root. Read `latest_codex_input.json` to locate the
newest completed run.

`fulfill-request` reads `codex_data_request.json` and atomically writes
`codex_data_response.json`. Requests contain six-digit codes and may ask for
daily fields, current-date minute fields, or both.

```json
{
  "schema_version": 1,
  "request_id": "example",
  "codes": ["000001"],
  "daily": {"lookback": 120, "fields": ["raw", "ma", "rsi", "macd", "atr"]},
  "minute": {"date": "YYYY-MM-DD", "start": "09:30", "end": "15:00", "fields": ["raw", "vwap"]}
}
```

Minute requests must use the current date. Historical minute data is not
available through this interface.

## Handoff

Each collection run writes `market_snapshot.csv`, `market_snapshot.json`,
`daily_indicators.csv`, `classification_map.csv`, `data_audit.json`,
`manifest.json`, and `workflow.log`. Inspect audit data, provider errors, and
freshness metadata before using the results.

## Limits

Providers may rate-limit or fail. Collected data can be partial, delayed, or
incomplete; use its coverage and freshness evidence rather than assuming it is
complete or current.

---
name: marketbase
description: "Collect objective A-share market data, daily history, neutral indicators, mappings, and audit evidence."
---

# MarketBase Data Collection

Use MarketBase when a Codex or cloud workflow needs local A-share source data.
It collects facts and writes auditable handoff files; interpretation and decisions
remain outside this local pipeline.

## Commands

```bash
marketbase --data-root ./data collect
marketbase --data-root ./data fulfill-request
```

`collect` retrieves a three-market snapshot, gathers up to 250 daily bars per
code, computes MA/RSI/MACD/ATR, builds basic mappings, and writes audit evidence.
It keeps caches and checkpoints under the data root. Read `latest_codex_input.json`
to locate the newest completed run.

`fulfill-request` reads `codex_data_request.json` and atomically writes
`codex_data_response.json`. Requests contain six-digit codes and one or both of:

```json
{
  "schema_version": 1,
  "request_id": "example",
  "codes": ["000001"],
  "daily": {"lookback": 120, "fields": ["raw", "ma", "rsi", "macd", "atr"]},
  "minute": {"date": "YYYY-MM-DD", "start": "09:30", "end": "15:00", "fields": ["raw", "vwap"]}
}
```

Minute requests must use the current date. Do not request historical minute data.

## Handoff

Each completed run has `market_snapshot.csv`, `market_snapshot.json`,
`daily_indicators.csv`, `classification_map.csv`, `data_audit.json`,
`manifest.json`, and `workflow.log`. Check `data_audit.json`, source errors, and
freshness metadata before consuming the data.

## Limits

Providers may rate-limit or fail. Do not treat collected data as investment advice
or as a guarantee of completeness, correctness, or timeliness.
complete or current.

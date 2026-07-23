# MarketBase

MarketBase is a local pipeline for collecting objective A-share market data.
It collects a three-market snapshot, up to 250 trading days of daily history,
neutral technical indicators, classification mappings, and data-audit records.
It supplies data only; interpretation and decisions belong to a separate Codex
or cloud workflow.

## Install

```bash
pip install .
# Optional China-market providers
pip install ".[data-cn]"
```

## Collect

```bash
marketbase --data-root ./data collect
# Equivalent source-tree command
python -m marketbase.cli --data-root ./data collect
```

The collection creates a timestamped run directory and maintains caches and
checkpoints so interrupted daily-history work can resume. Progress, estimated
remaining work, and provider errors are recorded in `workflow.log` and the audit
output. A successful run writes these seven handoff files:

- `market_snapshot.csv`
- `market_snapshot.json`
- `daily_indicators.csv`
- `classification_map.csv`
- `data_audit.json`
- `manifest.json`
- `workflow.log`

`latest_codex_input.json` at the data root always points to the newest completed
handoff. The snapshot includes current market facts; daily indicators are derived
from cached daily bars; the classification file holds basic mappings only.

## Request Additional Data

Create `codex_data_request.json` in the data root and run:

```bash
marketbase --data-root ./data fulfill-request
```

The command validates the request and atomically writes
`codex_data_response.json`. Daily requests may ask for `raw`, `ma`, `rsi`,
`macd`, or `atr` with a lookback of 1-250. Minute requests are restricted to the
current date and support raw rows or VWAP. Historical minute data is deliberately
not provided.

## Data Sources And Limits

Optional providers include Tencent and Sina HTTP endpoints, AkShare, Baostock,
and Tushare. Configure `TUSHARE_TOKEN` only when using Tushare. Providers can be
rate-limited, unavailable, delayed, or return incomplete data; MarketBase records
such errors and may use a cached last-good snapshot where supported. Inspect
`data_audit.json` before relying on a run.

This software supplies data collection and derived neutral indicators only. It is
not investment advice and does not guarantee data accuracy, timeliness, or
fitness for any purpose.

## Development

```bash
python -m pytest -q
python -m ruff check .
python -m build
```

See [SKILL.md](SKILL.md) for the agent-facing contract and
[README.zh-CN.md](README.zh-CN.md) for Chinese usage notes.

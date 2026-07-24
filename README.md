<div align="center">

# 📊 MarketBase

**A-Share Objective Data Pipeline**
<br>
<sub>Collect. Audit. Handoff. Never interpret.</sub>

<br>

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-211%20passed-34D058?style=for-the-badge&logo=pytest&logoColor=white)](https://github.com/Jameslzhang/marketbase)
[![License](https://img.shields.io/badge/License-MIT-586069?style=for-the-badge&logo=github&logoColor=white)](LICENSE)
[![Akshare](https://img.shields.io/badge/akshare-1.18-F7A81B?style=for-the-badge&logo=python&logoColor=white)](https://github.com/akshare/akshare)

</div>

<br>

---

## 🚀 Quick Start

<table>
<tr>
<td width="50%">

### ① Install

```bash
git clone https://github.com/Jameslzhang/marketbase.git
cd marketbase
pip install ".[data-cn]"
```

</td>
<td width="50%">

### ② Collect

```bash
# Full pipeline — one command
python local_workflow.py

# Or with explicit data root
python local_workflow.py --data-root ./my_data collect
```

</td>
</tr>
</table>

---

## 🧩 Modules

<table>
<tr>
<td width="33%" align="center" bgcolor="#f0f7ff">

### 📅 Calendar
`calendar.py`

Holiday-aware trading calendar
<br>
<sub>akshare → local cache</sub>

</td>
<td width="33%" align="center" bgcolor="#f0fff0">

### 🏷️ Security Master
`security_master.py`

5,500+ stocks · code · name
<br>
<sub>Market · listing date · status</sub>

</td>
<td width="33%" align="center" bgcolor="#fff7f0">

### 📊 Market Snapshot
`market_collector.py`

Three-market real-time
<br>
<sub>SH · SZ · BSE</sub>

</td>
</tr>
<tr>
<td width="33%" align="center" bgcolor="#f5f0ff">

### 📈 Daily History
`daily_collector.py`

250 trading days
<br>
<sub>Checkpoint · resume · multi-source</sub>

</td>
<td width="33%" align="center" bgcolor="#fff0f0">

### ⏱️ Minute Data
`minute_collector.py`

Current-day minute bars
<br>
<sub>VWAP included</sub>

</td>
<td width="33%" align="center" bgcolor="#f0fff5">

### 📐 Indicators
`indicators.py`

MA · RSI · MACD · ATR
<br>
<sub>Neutral · derived only</sub>

</td>
</tr>
<tr>
<td width="33%" align="center" bgcolor="#faf5ff">

### 🗂️ Classification
`classify.py` + `classification_collector.py`

Industry · concept · supply chain
<br>
<sub>EastMoney xuangu API</sub>

</td>
<td width="33%" align="center" bgcolor="#fff5f0">

### 🔍 Data Audit
`data_audit.py`

Coverage · freshness · integrity
<br>
<sub>Per-run evidence</sub>

</td>
<td width="33%" align="center" bgcolor="#f0f5ff">

### 📡 Volume Ratio
`volume_ratio.py`

Snapshot + daily cache
<br>
<sub>Real-time computation</sub>

</td>
</tr>
</table>

---

## ⚡ Commands

<table>
<tr>
<td bgcolor="#f6f8fa">

```bash
# Full collection — snapshot, history, indicators, audit
python local_workflow.py
```

</td>
<td bgcolor="#f6f8fa">

```bash
# Refresh security master (5,500+ stocks)
python local_workflow.py refresh-master
```

</td>
</tr>
<tr>
<td bgcolor="#f6f8fa">

```bash
# Collect industry/concept classification
python local_workflow.py collect-classify
```

</td>
<td bgcolor="#f6f8fa">

```bash
# Fulfill a Codex data request
python local_workflow.py fulfill-request
```

</td>
</tr>
</table>

> 💡 All commands support `--data-root` to override the default data directory.

---

## 📁 Handoff Files

Every successful run produces a timestamped directory with these artifacts:

<table>
<tr>
<td>

```
📦 run_20260724_102708/
├── 📊 market_snapshot.csv
├── 📊 market_snapshot.json
├── 📈 daily_indicators.csv
├── 🗂️ classification_map.csv
├── 🔍 data_audit.json
├── 📋 manifest.json
└── 📝 workflow.log
```

</td>
<td width="40%">

> 🔗 `latest_codex_input.json`
> always points to the newest
> completed handoff.

</td>
</tr>
</table>

---

## 📡 Request Additional Data

<blockquote>
<strong>① Create</strong> <code>codex_data_request.json</code> in the data root<br>
<strong>② Run</strong> <code>python local_workflow.py fulfill-request</code><br>
<strong>③ Read</strong> <code>codex_data_response.json</code>
</blockquote>

| Field | Options | Lookback |
|-------|---------|----------|
| Daily | `raw` `ma` `rsi` `macd` `atr` | 1–250 days |
| Minute | `raw` `vwap` | Current day only |

> ⚠️ Historical minute data is deliberately **not** provided.

---

## ⚙️ Data Sources

| Provider | Type | Notes |
|----------|------|-------|
| 🌐 Tencent HTTP | Free | Snapshot, daily |
| 🌐 Sina HTTP | Free | Snapshot, daily |
| 📦 AkShare | Free | Calendar, stock list |
| 📦 Baostock | Free | Daily history |
| 🔑 Tushare | Token | `TUSHARE_TOKEN` env |

> ⚠️ Providers may rate-limit, delay, or return incomplete data. Inspect `data_audit.json` before relying on a run.

---

## 🛠️ Development

<table>
<tr>
<td bgcolor="#f0f7ff" align="center">

```bash
python -m pytest -q
```
<sub>**211** tests</sub>

</td>
<td bgcolor="#f0fff0" align="center">

```bash
python -m ruff check .
```
<sub>Lint</sub>

</td>
<td bgcolor="#fff7f0" align="center">

```bash
python -m build
```
<sub>Package</sub>

</td>
</tr>
</table>

<br>

<div align="center">

📖 [SKILL.md](SKILL.md) — Agent contract &nbsp;|&nbsp; 🇨🇳 [中文说明](README.zh-CN.md)

<sub>This software supplies data collection and neutral indicators only. Not investment advice.</sub>

</div>

<div align="center">

[English](README.md) | [简体中文](README.zh-CN.md)

<br>

# 📊 MarketBase

**A 股客观数据采集管道**
<br>
<sub>采集 · 审计 · 交接 · 不解释</sub>

<br>

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Tests](https://img.shields.io/badge/测试-530%20通过-34D058?style=for-the-badge&logo=pytest&logoColor=white)](https://github.com/Jameslzhang/marketbase)
[![License](https://img.shields.io/badge/许可-Apache--2.0-586069?style=for-the-badge&logo=github&logoColor=white)](LICENSE)
[![Akshare](https://img.shields.io/badge/akshare-1.18-F7A81B?style=for-the-badge&logo=python&logoColor=white)](https://github.com/akshare/akshare)

</div>

<br>

## 🚀 快速开始

<table>
<tr>
<td width="50%">

### ① 安装

```bash
git clone https://github.com/Jameslzhang/marketbase.git
cd marketbase
pip install ".[data-cn]"
```

</td>
<td width="50%">

### ② 采集

```bash
# 一键全量采集
python local_workflow.py

# 或指定数据目录
python local_workflow.py --data-root ./my_data collect
```

</td>
</tr>
</table>

---

## 🧩 模块

<table>
<tr>
<td width="33%" align="center" bgcolor="#f0f7ff">

### 📅 交易日历
`calendar.py`

节假日感知的交易日历
<br>
<sub>akshare → 本地缓存</sub>

</td>
<td width="33%" align="center" bgcolor="#f0fff0">

### 🏷️ 证券主表
`security_master.py`

5,500+ 股票 · 代码 · 名称
<br>
<sub>市场 · 上市日期 · 状态</sub>

</td>
<td width="33%" align="center" bgcolor="#fff7f0">

### 📊 实时快照
`market_collector.py`

三市实时行情
<br>
<sub>沪 · 深 · 北</sub>

</td>
</tr>
<tr>
<td width="33%" align="center" bgcolor="#f5f0ff">

### 📈 日线历史
`daily_collector.py`

250 个交易日
<br>
<sub>断点续跑 · 多源降级</sub>

</td>
<td width="33%" align="center" bgcolor="#fff0f0">

### ⏱️ 分钟数据
`minute_collector.py`

当日分钟线
<br>
<sub>含 VWAP</sub>

</td>
<td width="33%" align="center" bgcolor="#f0fff5">

### 📐 技术指标
`indicators.py`

MA · RSI · MACD · ATR
<br>
<sub>中性 · 仅派生</sub>

</td>
</tr>
<tr>
<td width="33%" align="center" bgcolor="#faf5ff">

### 🗂️ 分类映射
`classify.py` + `classification_collector.py`

行业 · 概念 · 产业链
<br>
<sub>东方财富选股 API</sub>

</td>
<td width="33%" align="center" bgcolor="#fff5f0">

### 🔍 数据审计
`data_audit.py`

覆盖率 · 时效性 · 完整性
<br>
<sub>每次运行均有证据</sub>

</td>
<td width="33%" align="center" bgcolor="#f0f5ff">

### 📡 量比计算
`volume_ratio.py`

快照 + 日线缓存
<br>
<sub>实时计算</sub>

</td>
</tr>
</table>

---

## ⚡ 扫描与定时任务

除数据管道外，MarketBase 还内置全盘 T+1 快速扫描器和有界定时运行器：

<table>
<tr>
<td width="50%" bgcolor="#f6f8fa">

**🔎 快速 T+1 扫描** — `fast_t1_scan.py`

一条命令完成全盘扫描：快照 → 指标（量比单遍并入）→ 评分 → HTML 报告。
跳过重量级的审计/分类阶段以提速；当日快照在新鲜窗口内直接复用。

```bash
python fast_t1_scan.py            # 快照 15 分钟内复用
python fast_t1_scan.py --fresh 0  # 强制重新采集
```

</td>
<td width="50%" bgcolor="#f6f8fa">

**⏰ 定时管道** — `marketbase/scheduled_pipeline.py`

将 采集 → 固定交接 → 扫描 → 决策 → 报告 封装为单个有界作业：
每阶段超时控制、非交易日预检、每次运行落盘 `result.json` 证据。
可配合 Windows 任务计划 / cron 使用。

```bash
python -m marketbase.scheduled_pipeline --slot 1530
```

</td>
</tr>
</table>

---

## ⚡ 命令

<table>
<tr>
<td bgcolor="#f6f8fa">

```bash
# 全量采集 —— 快照、日线、指标、审计
python local_workflow.py
```

</td>
<td bgcolor="#f6f8fa">

```bash
# 刷新证券主表（5,500+ 只股票）
python local_workflow.py refresh-master
```

</td>
</tr>
<tr>
<td bgcolor="#f6f8fa">

```bash
# 采集行业/概念分类
python local_workflow.py collect-classify
```

</td>
<td bgcolor="#f6f8fa">

```bash
# 响应 Codex 数据请求
python local_workflow.py fulfill-request
```

</td>
</tr>
</table>

> 💡 所有命令均支持 `--data-root` 覆盖默认数据目录。

---

## 📁 交接文件

每次成功运行生成带时间戳的目录，包含以下制品：

<table>
<tr>
<td>

```
📦 run_20260724_102708/
├── 📊 market_snapshot.csv
├── 📊 market_snapshot.json
├── 📈 daily_indicators.csv
├── 📊 index_data.csv
├── 📈 industry_agg.csv
├── 📈 market_breadth.json
├── 🗂️ classification_map.csv
├── ⏱️ intraday_minutes.parquet
├── 🔍 data_audit.json
├── 📋 manifest.json
├── 📋 run_status.json
└── 📝 workflow.log
```

</td>
<td width="40%">

> 🔗 `latest_codex_input.json`
> 指向最新完成的数据交接。
>
> 🔗 `latest_full_ready.json`
> 指向数据质量完整（full）的最新运行。
>
> 🔗 `latest_static_ready.json`
> 指向静态数据齐备（static）的最新运行。
>
> 🔗 `latest_complete.json`
> 兼容旧版 —— 始终指向最新可用运行。

</td>
</tr>
</table>

---

## 📡 按请求补数

<blockquote>
<strong>① 创建</strong> <code>codex_data_request.json</code> 放在数据根目录<br>
<strong>② 运行</strong> <code>python local_workflow.py fulfill-request</code><br>
<strong>③ 读取</strong> <code>codex_data_response.json</code>
</blockquote>

| 字段 | 可选值 | 回看范围 |
|------|--------|----------|
| 日线 | `raw` `ma` `rsi` `macd` `atr` | 1–250 日 |
| 分钟 | `raw` `vwap` | 仅限当日 |

> ⚠️ 历史分钟数据**不提供**。

---

## ⚙️ 数据源

| 数据源 | 类型 | 说明 |
|--------|------|------|
| 🌐 腾讯 | 免费 HTTP | 快照、日线 |
| 🌐 新浪 | 免费 HTTP | 快照、日线 |
| 📦 AkShare | 免费 | 交易日历、股票列表 |
| 📦 Baostock | 免费 | 日线历史 |
| 🔑 Tushare | Token | 需设置 `TUSHARE_TOKEN` |

> ⚠️ 数据源可能限流、延迟或返回不完整数据。使用前检查 `data_audit.json`。

---

## 🛠️ 开发验证

<table>
<tr>
<td bgcolor="#f0f7ff" align="center">

```bash
python -m pytest -q
```
<sub>**530** 个测试</sub>

</td>
<td bgcolor="#f0fff0" align="center">

```bash
python -m ruff check .
```
<sub>代码检查</sub>

</td>
<td bgcolor="#fff7f0" align="center">

```bash
python -m build
```
<sub>打包</sub>

</td>
</tr>
</table>

<br>

<div align="center">

📖 [SKILL.md](SKILL.md) — Agent 接口说明

<br>

[English](README.md) | [简体中文](README.zh-CN.md)

<br>

<sub>本项目仅提供数据采集和中性派生指标，不构成投资建议。</sub>

</div>

# AlphaSift

AlphaSift 是本地 A 股客观数据采集管道。它采集沪深北三市实时快照、最多 250 个交易日的日线、
中性技术指标、基础分类映射与数据审计记录。本地只提供数据交接；解释与决策由 Codex 或云端流程
独立完成。

## 安装与采集

```bash
pip install .
# 可选：中国数据源依赖
pip install ".[data-cn]"

alphasift --data-root ./data collect
# 源码树中的等价命令
python -m alphasift.cli --data-root ./data collect
```

采集会创建带时间戳的运行目录。日线缓存和检查点支持断点续跑；进度、预计剩余工作量和数据源错误
会写入 `workflow.log` 与审计结果。每次完成的运行目录包含以下 7 个交接文件：

- `market_snapshot.csv`
- `market_snapshot.json`
- `daily_indicators.csv`
- `classification_map.csv`
- `data_audit.json`
- `manifest.json`
- `workflow.log`

数据根目录的 `latest_codex_input.json` 始终指向最新完成交接。快照保存当前市场事实，日线指标由
已缓存日线计算，分类文件仅保存基础映射。

## 按请求补数

在数据根目录创建 `codex_data_request.json` 后执行：

```bash
alphasift --data-root ./data fulfill-request
```

命令会严格校验请求，并以原子方式写出 `codex_data_response.json`。日线可请求 `raw`、`ma`、
`rsi`、`macd`、`atr`，回看范围为 1-250。分钟线仅允许当天，字段仅为原始行与 VWAP；不提供
历史分钟数据。

## 缓存、来源与限制

可选数据源包括腾讯和新浪 HTTP 接口、AkShare、Baostock 与 Tushare；使用 Tushare 时设置
`TUSHARE_TOKEN`。数据源可能限流、不可用、延迟或返回不完整数据。程序会记录错误，并在支持的
场景使用最近一次可用快照；使用前应检查 `data_audit.json`。

本项目只提供数据采集和中性派生指标，不构成投资建议，也不保证数据的准确性、时效性或适用性。

## 开发验证

```bash
python -m pytest -q
python -m ruff check .
python -m build
```

Agent 接口说明见 [SKILL.md](SKILL.md)。

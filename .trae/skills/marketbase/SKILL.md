---
name: marketbase
description: "Collect objective A-share market data, daily history, neutral indicators, mappings, and audit evidence."
---

# MarketBase Data Collection

## 描述

MarketBase 是 A 股客观行情数据采集管线。当 Codex 或云端工作流需要本地 A 股数据源时使用本 skill。
它只负责采集事实并写入可审计的交接文件，不进行任何解释或决策。

## 使用场景

- 需要采集 A 股三市实时行情快照
- 需要获取全市场日线历史数据（250 日）
- 需要计算中性技术指标（MA、RSI、MACD、ATR、BOLL、RPS）
- 需要构建行业/概念/产业链分类映射
- 需要执行数据质量审计
- 需要响应 Codex 数据请求协议

## 指令

### 1. 全量数据采集

执行以下命令采集全量数据：

```bash
marketbase --data-root ./data collect
```

采集流程：
1. 文件锁防多实例
2. 实时行情快照（沪深 + 北交所独立采集）
3. 分钟快照追加至 `intraday_1m.parquet`
4. 全市场日线历史采集（15 线程并发，断点续跑）
5. 技术指标计算（MA/RSI/MACD/ATR/BOLL/回报/影线/RPS20）
6. 本地量比计算
7. 交易可执行性标注（ST/停牌/涨跌停）
8. 数据质量审计
9. 分类映射构建
10. 市场广度 + 行业 MA 分布
11. 输出制品 + manifest + latest 指针

### 2. 数据请求响应

```bash
marketbase --data-root ./data fulfill-request
```

读取 `codex_data_request.json` 并原子化写入 `codex_data_response.json`。

请求格式：
```json
{
  "schema_version": 1,
  "request_id": "example",
  "codes": ["000001"],
  "daily": {"lookback": 120, "fields": ["raw", "ma", "rsi", "macd", "atr"]},
  "minute": {"date": "YYYY-MM-DD", "start": "09:30", "end": "15:00", "fields": ["raw", "vwap"]}
}
```

分钟请求只能使用当前日期，不提供历史分钟数据。

### 3. 分类采集

```bash
marketbase --data-root ./data collect-classify
```

### 4. 刷新证券主表

```bash
marketbase --data-root ./data refresh-master
```

### 5. T+1 策略分析

```bash
marketbase --data-root ./data t1-analyze
```

## 交接文件

每次运行在 `run_YYYYMMDD_HHMMSS/` 目录下生成：
- `market_snapshot.csv` / `market_snapshot.json` — 全市场快照
- `daily_indicators.csv` — 日线技术指标
- `classification_map.csv` — 行业/概念映射
- `market_breadth.json` — 市场广度
- `industry_ma_distribution.json` — 行业 MA 分布
- `data_audit.json` — 数据审计报告
- `manifest.json` — 制品清单（含 SHA256）
- `workflow.log` — 运行日志

使用数据前应检查 `data_audit.json`、源错误和新鲜度元数据。

### 6. T+1 全生命周期策略快照（V2）

```bash
python -m marketbase.t1_snapshot --v2
```

输出 `strategies/t1_processed_data_v2.json`，包含：
- 通道识别（趋势延续/强势回踩/高动量/板块反转）
- 双轴判断（机会质量×尾部风险 9 格矩阵）
- 入场状态机（14 个状态）
- 附录 A 买区/卖区/保护位/手续费
- 上影线/行业同步/市场环境
- 审计追踪

**重要**：此文件为 Skill 的只读数据源。Skill 必须读取此文件中的确定性结论，不得自行计算。

## 约束

- 数据源可能限流或失败，不保证数据完整性、正确性或时效性
- 采集的数据不构成投资建议
- 本地管线不输出候选池、排名、筛选、推荐或交易结论
- 日线历史固定保存最近 250 个交易日
- 分钟请求只允许当前交易日
- 指标只输出数值和计算元数据，不输出方向标签或信号分

## 策略管线边界（Python 管判，Skill 管说）

本地 Python 负责所有确定性计算：
- VWAP、ATR、MA、RSI、MACD、BOLL、RPS
- 买区、禁追价、保护位、卖区
- 通道识别、双轴判断
- 入场/退出状态机转换
- 利润保护、审计对账

云端 Skill 负责只读消费：
- 读取 `t1_processed_data_v2.json` 中的确定性结论
- 生成用户可读分析文案
- 提供定性解读和宏观背景
- 不得修改任何数值、状态或决策
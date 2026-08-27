---
name: superpowers
description: "MarketBase objective data pipeline design spec and step-by-step implementation plan with task tracking."
---

# MarketBase Superpowers — 客观数据管线实施计划

Superpowers 包含 MarketBase 项目的设计规格和逐步实施计划。当需要按照计划执行任务、检查进度、或了解设计决策时使用本 skill。

## 文档

| 文档 | 路径 |
|------|------|
| 设计规格 | `docs/superpowers/specs/2026-07-22-objective-data-pipeline-design.md` |
| 实施计划 | `docs/superpowers/plans/2026-07-22-objective-data-pipeline-implementation.md` |

## 职责边界

**本地运行端负责：**
- 采集全市场实时价格、成交量、成交额、换手率和量比。
- 保存最近 250 个交易日的原始日线。
- 按明确请求保存当前交易日的原始分钟线。
- 计算中性的数学指标：VWAP、MA、RSI、MACD 和 ATR。
- 保存行业、概念和产业链基础映射。
- 校验行数、时间、覆盖率、重复代码和接口错误。

**本地运行端不得：**
- 建立候选池或选择固定前 N 只股票。
- 解释行业、题材、指标或事件的好坏。
- 排名、筛选、拒绝、推荐或执行遗漏分析。
- 输出策略分、信号分、推荐标签。

## 任务总览

| 任务 | 描述 | 状态 |
|------|------|------|
| 1 | 中性数学指标 (neutral_indicators.py) | ⬜ |
| 2 | Codex 数据请求与响应协议 (data_request.py) | ⬜ |
| 3 | 全量日线缓存、限流和断点恢复 (daily_collector.py) | ⬜ |
| 4 | 实时行情采集和独立审计 (market_collector.py, data_audit.py) | ⬜ |
| 5 | 行业、概念和产业链基础映射 (classification_map.py) | ⬜ |
| 6 | 当前交易日分钟数据与 Codex 响应 (minute_collector.py) | ⬜ |
| 7 | 重写一键入口和输出协议 (local_workflow.py) | ⬜ |
| 8 | 删除本地策略链路并更新文档和打包 | ⬜ |

## 实施方法

每个任务遵循 TDD 流程：
1. 先写失败测试
2. 运行测试确认失败
3. 实现功能
4. 运行测试和 Ruff
5. 暂存并提交

## 全局约束

- 本地不得建立候选池、排名、筛选、拒绝、推荐或交易结论。
- 日线历史固定保存最近 250 个交易日。
- 分钟请求只允许当前交易日。
- 指标只输出数值和计算元数据，不输出方向标签或信号分。
- 全量日线刷新必须支持断点续跑、同日缓存命中、多源降级和原子写入。
- 控制台与日志只使用数据采集措辞。
# 策略全生命周期 Skill 适配合约

版本：2.0.0-dev | 日期：2026-07-31 | 设计稿：V1.6

## 0. 核心原则

```
本地 Python 管"算"+"判"          云端 Skill 管"说"
──────────────────────────      ──────────────────
1. 采集全市场数据                5. 读取 processed_data_v2.json
2. 计算所有指标（附录A公式）       6. 生成用户可读分析文案
3. 执行状态机（入场+退出）         7. 解释状态转换原因（定性）
4. 执行通道识别+双轴判断          8. 提供宏观/行业背景
5. 输出 processed_data_v2.json   9. 输出决策理由+风险提示
                                 10. 绝不修改 Python 层确定性结论
```

## 1. 禁止事项（违反即失败，测试用例32）

任一 Skill 若执行以下操作，适配测试必须失败：

1. ❌ 自行计算 VWAP、ATR、保护位、卖区或状态标签
2. ❌ 绕过 `ObjectiveDataProvider` 读取原始数据
3. ❌ 修改 `processed_data_v2.json` 中的确定性结论
4. ❌ 跳过状态机转换（直接发布买点）
5. ❌ 把 `conditional_watch` 表述为"可以买"
6. ❌ 在 `confirmed_candidate` 状态发布执行计划
7. ❌ 使用自然语言覆盖 `market_veto` 的否决
8. ❌ 在 `data_insufficient` 状态下推断补值

## 2. Skill 只读数据源

所有 Skill 的统一输入：`strategies/t1_processed_data_v2.json`

```json
{
  "meta": {
    "strategy_profile_id": "watchlist_t1_v1",
    "strategy_version": "2.0.0-dev",
    "formula_version": "2.0.0",
    "lifecycle_contract_version": "1.6.0",
    ...
  },
  "market": { "advance_ratio": 0.45, ... },
  "summary": { "candidates": 3, "conditional": 2, "rejected": 18 },
  "stocks": [
    {
      "code": "002353",
      "computed_v2": {
        "primary_channel": "trend_continuation",
        "dual_axis": { "decision": "can_enter_candidate", ... },
        "buy_zone": { "lower": 100.0, "upper": 102.0, ... },
        "sell_zone": { "first_lower": 105.0, ... },
        "protection": { "price": 98.0, "constructible": true },
        "entry_state": "deep_watch",
        "channels": { ... },
        "fees": { ... },
        "industry_sync": { "pass": true, ... },
        "market": { "veto": false, ... }
      }
    }
  ]
}
```

## 3. 8 个 Skill 合约

### 3.1 a-share-t1-rank-sandbox — 候选覆盖与双轴判断

**读取**：`stocks[].computed_v2.dual_axis`、`stocks[].computed_v2.primary_channel`

**输出**：
- 按 `decision` 分类：`can_enter_candidate` / `conditional_watch` / `reject`
- 按 `opportunity_quality` 降序排列候选
- 生成自然语言摘要："今日共N只候选，其中M只正式候选，K只条件观察"

**禁止**：
- 修改 `dual_axis.decision` 的值
- 把 `conditional_watch` 提升为 `can_enter_candidate`
- 自行计算机会质量评分

### 3.2 a-share-quant-sandbox — 量化复核

**读取**：`stocks[].computed_v2.buy_zone`、`stocks[].computed_v2.sell_zone`、`stocks[].computed_v2.protection`、`stocks[].computed_v2.fees`

**输出**：
- 复查买区/卖区/保护位的合理性
- 展示手续费后收益风险比
- 生成复核清单："✓ 买区构造通过 / ✓ 保护位可构造 / ✓ 收益风险比≥1.20"

**禁止**：
- 重新计算 VWAP、ATR、买区、保护位、卖区
- 修改 `buy_zone` 或 `sell_zone` 的数值
- 基于"感觉太贵"修改保护位

### 3.3 a-share-technical-lab — 技术面研判

**读取**：`stocks[].computed_v2.daily_context`、`stocks[].computed_v2.upper_shadow`、`stocks[].computed_v2.channels`

**输出**：
- 描述趋势结构（多头/空头排列）
- 描述波动特征（ATR 百分位、上影线模式）
- 生成技术面标签："趋势多头排列，RSI 62 健康区，BOLL 中轨上方"

**禁止**：
- 重新计算 MA、RSI、MACD、BOLL
- 修改趋势判断结论
- 基于图表形态覆盖确定性指标

### 3.4 a-share-prediction-audit — 四类失败归因

**读取**：`stocks[].computed_v2.dual_axis.reason_code`、`stocks[].computed_v2.entry_state`、`audit.metrics`

**输出**：
- 按四类责任分类：selection_failure / entry_failure / exit_failure / execution_failure
- 统计各通道的命中率
- 生成审计报告："今日拒绝12只，其中通道失败X只，市场否决Y只，双轴不达标Z只"

**禁止**：
- 修改 `reason_code` 的值
- 把结构性失败归为数值参数问题
- 在数据不足时推断原因

### 3.5 a-share-portfolio-optimizer — 持仓生命周期

**读取**：需要持仓状态账本（独立于 processed_data_v2.json）

**输出**：
- 展示当前持仓状态（T+1锁定/开盘观察/盘中监控/退出已触发）
- 展示次日退出计划
- 执行对冲式加仓检查

**禁止**：
- 覆盖退出状态机的转换
- 在保护位失守后输出原推荐目标
- 允许对冲式加仓

### 3.6 a-share-watchlist-shortline — 固定自选池入场与次日退出

**读取**：`stocks[].computed_v2`（仅 `strategy_profile_id=watchlist_t1_v1`）

**输出**：
- 展示正式候选的买区、禁追价、保护位、卖区
- 展示入场状态（deep_watch / conditional_watch / confirmed_candidate / entry_active）
- 生成次日退出计划

**禁止**：
- 在 `entry_active` 之前发布可执行买点
- 把 `conditional_watch` 表达为"挂单价"
- 跳过状态机转换

### 3.7 a-share-watchlist-shortline-audit — 次日开盘至14:30退出审计

**读取**：需要持仓退出状态账本

**输出**：
- 展示退出触发时间和类型
- 展示实际成交 vs 理论可执行价
- 生成退出审计报告

**禁止**：
- 把理论止损价冒充实际成交
- 在 `exit_unavailable` 时声称已退出

### 3.8 full-market-t1-strategy — 全市场T+1策略

**读取**：`stocks[].computed_v2`（`strategy_profile_id=full_market_t1_v1`）

**输出**：
- 与 a-share-watchlist-shortline 相同结构
- 但候选宇宙更大，通道集合不同

**禁止**：
- 跨配置读取候选名额
- 把同一交易同时记入两个配置

## 4. Skill 输出模板

每个 Skill 的输出必须包含：

```markdown
## {股票代码} {股票名称}

**状态**: {entry_state 的用户可见映射}
**通道**: {primary_channel}
**双轴**: 机会{opportunity_quality} × 风险{tail_risk} = {decision}

**买区**: {buy_zone_lower} - {buy_zone_upper}
**禁追价**: {no_chase_price}
**保护位**: {protection_price}
**卖区1**: {first_zone_lower} - {first_zone_upper}
**卖区2**: {second_zone_lower} - {second_zone_upper}（如有）

**收益风险比**: {reward_risk_ratio}
**手续费后净收益**: {net_reward}

**分析**: [Skill 生成的定性解读，不得修改上述数值]
**风险提示**: 以上为策略分析，不构成投资建议。市场有风险，投资需谨慎。
```

## 5. Skill 分批适配顺序

| 批次 | Skills | 依赖 | 验收 |
|------|--------|------|------|
| 第一批 | 新建只读适配 Skill | Provider + Schema | 能正确读取 V2 JSON |
| 第二批 | rank-sandbox, quant-sandbox, technical-lab | 状态机 + 附录A公式 | 文案不覆盖数值 |
| 第三批 | prediction-audit, portfolio-optimizer | 审计 + 对账 | 归因分类正确 |
| 第四批 | watchlist-shortline, watchlist-shortline-audit, full-market-t1 | 全部 | 端到端集成 |
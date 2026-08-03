# 策略模型版本文件

版本：2.0.0-dev | 日期：2026-07-31 | 设计稿：V1.6

## 当前版本

```yaml
strategy_profile_id: "watchlist_t1_v1"
strategy_version: "2.0.0-dev"
lifecycle_contract_version: "1.6.0"
schema_version: "2.0.0"
execution_rule_version: "2.0.0-dev"
skill_version: "2.0.0-dev"
```

## 版本历史

| 版本 | 日期 | 变更 | 状态 |
|------|------|------|------|
| 1.0.0 | 2026-07-23 | 初始版本：固定自选池三通道短线策略 | 生产 |
| 2.0.0-dev | 2026-07-31 | 全生命周期升级：入场/退出状态机、双轴判断、利润保护、影子运行 | 开发中 |

## 参数版本

| 参数组 | 版本 | 校准状态 | 值 |
|--------|------|----------|-----|
| profit_protection | shadow_v1 | 未校准（影子） | target_progress=0.80, drawdown=0.50×ATR, confirm=2min, activity_max=0.65 |
| buy_zone | v2.0.0 | 已冻结 | 附录A.3 BUY_ZONE_V1 |
| sell_zone | v2.0.0 | 已冻结 | 附录A.4 SELL_ZONES_V1 |
| protection | v2.0.0 | 已冻结 | 附录A.4 PROTECTION_V1 |
| fees | default_v1 | 已冻结 | 佣金0.03%/印花税0.05%/过户费0.001% |
| no_chase | v2.0.0 | 已冻结 | 附录A.3 NO_CHASE_V1 |
| activity | v2.0.0 | 已冻结 | 附录A.3 ACTIVITY_HOLD_V1 |

## 通道状态

| 通道 | 配置 | 生产可买 | 样本要求 |
|------|------|----------|----------|
| trend_continuation | watchlist + full_market | ✅ | 已验收 |
| strong_pullback_reclaim | watchlist + full_market | ✅ | 已验收 |
| high_momentum | 仅影子 | ❌ | HIGH_MOMENTUM_V1 合同待写入 |
| sector_reversal_challenger | 仅影子 | ❌ | 影子样本不足 |

## 回滚条件

- 任一结构性测试失败 → 不得设置 effective_from
- 正式候选率显著下降且尾部损失未改善 → 验收失败
- 影子运行不足5个交易日 → 不得切换生产
- 同一 decision_id 版本不一致 → 立即回退

## 下次切换窗口

- 影子运行完成5个交易日
- 利润保护参数达到校准门槛（60样本/3通道/20交易日）
- 周四或周五切换，周末复盘窗口
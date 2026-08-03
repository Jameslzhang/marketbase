# T+1 策略全生命周期自动化提示词

版本：2.0.0-dev | 日期：2026-07-31

---

## 每日自动化任务

### 1. 盘前数据采集（09:25 前）

```bash
cd D:/Environment/marketbase
python local_workflow.py collect
python local_workflow.py build-t1-snapshot --v2
```

验证：
- [ ] `data/daily_runs/{date}/` 存在
- [ ] `strategies/t1_processed_data_v2.json` 存在
- [ ] `meta.quality_status == "data_ready"`
- [ ] `stocks` 数量 >= 20

### 2. 候选筛选（09:25-09:30）

**读取** `strategies/t1_processed_data_v2.json`

**条件**：
- `dual_axis.decision == "can_enter_candidate"` → 正式候选
- `dual_axis.decision == "conditional_watch"` → 条件观察
- `entry_state == "deep_watch"` → 深度观察

**输出**：候选列表（按机会质量降序）

### 3. 盘中监控（09:30-14:57）

**读取** 分钟数据（通过 `fulfill-request` 协议）

**条件**：
- 连续3根完成分钟确认
- 价格在买区、不高于禁追价
- VWAP 位置确认
- 活动度满足
- 行业/市场证据正常

**状态转换**：由 Python 状态机执行，Skill 只读取状态

### 4. 次日持仓管理（次日 09:25-14:30）

**条件**：
- 开盘验证（正常/强势/弱势/跳空越过保护位）
- 盘中监控（卖区触发/保护位失守/利润保护/14:30时间退出）
- 退出执行（可成交/不可成交/用户偏离）

**禁止**：
- 把未成交的理论价格记为实际成交
- 在 `exit_unavailable` 时声称已退出
- 在 `execution_unknown` 时推定未成交

### 5. 盘后审计（收盘后）

**生成**：当日审计报告
- 正式候选率
- 条件观察转化率
- 退出执行率
- 四类失败归因统计
- 手续费后收益概览

---

## 自动化约束

```
禁止事项：
1. 任何自动化任务不得绕过 ObjectiveDataProvider
2. 任何自动化任务不得自行计算指标
3. 任何自动化任务不得修改 processed_data_v2.json
4. 任何自动化任务不得跳过状态机转换
5. 任何自动化任务不得在 data_insufficient 时推断补值
6. 任何自动化任务不得生成未版本化的结论
```

## 版本字段

所有决策记录必须包含：
- `strategy_profile_id`
- `strategy_version`
- `lifecycle_contract_version`
- `schema_version`
- `execution_rule_version`
- `skill_version`

## 应急处理

若 Provider 快照异常：
1. 标记 `data_insufficient`
2. 不得降级使用旧数据
3. 不得用自然语言补值
4. 等待数据恢复或到达截止时间

若状态机卡死：
1. 停止生成新版决策
2. 回退到上一通过验收的版本
3. 已产生的 decision_id 按冻结版本完成退出和审计
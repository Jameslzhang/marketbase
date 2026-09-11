# 全市场 T+1 最终验收阻断修复报告

- 执行时间：2026-09-03 18:29:48 +08:00
- 提交：`e191847e7e9a789fad3d44916570229d44ad9e40`
- 提交说明：`fix: close full-market T1 release blockers`

## RED 证据

先新增最小行为回归并实际运行：

```text
pytest tests/test_fast_t1_scan.py::test_main_writes_candidate_union_with_scan_metadata
       tests/test_full_market_t1.py::test_buyable_requires_both_scores_and_all_hard_gates
       tests/test_full_market_t1.py::test_global_data_not_ready_vetoes_every_production_action_but_keeps_audit_rows -q

3 failed
- 真实 main 路径旧过滤将 4 个边界样本过滤为 0 个，40、49.99、50 均未进入候选并集。
- evaluate_candidate 没有 strategy_channel，证明生命周期未接入。
- global_status=data_not_ready 时 only_choose_one 仍错误返回 600001。
```

另补全局状态机语义回归后再次看到预期 RED：候选的 `entry_state` 仍为 `entry_active`，而不是市场否决后的 `rejected`。

真实生命周期字段回归先看到预期 RED：主流程候选缺少 `rps20`，证明旧路径没有生成完整生命周期输入。

## GREEN 实现

1. 快扫上游价格门槛改为 `>=40`，候选并集保留 40–49.99 影子带；正式报告/观察名单继续只消费 `>=50`，50 元边界进入 production，低于 40 排除。
2. `ma11`、`ma23`、`momentum_delta_1/3`、`repeated_upper_shadow` 直接来自 `compute_daily_indicators`；`rps20` 由 `compute_rps20` 在合并后的横截面生成。未使用 MA10/MA20 或人工动量近似。
3. `evaluate_candidate` 复用真实 `ChannelIdentifier`、`DualAxis`、`EntryStateMachine`；每行稳定输出 `strategy_channel`、通道证据、`dual_axis`、`entry_state` 和状态轨迹。
4. 客观生命周期字段缺失时输出 `channel_evidence.status=data_insufficient`、`strategy_channel=unresolved`、`dual_axis.status=not_evaluated`，并以 `lifecycle_data_insufficient` 封锁买入资格。
5. 全局 `critical_ready=false` 同时进入生命周期 `market_veto`，所有候选强制关闭三个资格布尔值，`only_choose_one=null`、正式 executable 为空，审计行和 `global_data_not_ready` 原因码保留。
6. 冻结门槛未变：机会分 65、执行分 68。

## GREEN / 回归证据

提交后运行指定目标回归：

```text
.venv\Scripts\python.exe -m pytest tests/test_full_market_t1.py tests/test_fast_t1_scan.py tests/test_local_workflow.py tests/test_strategy_lifecycle.py -q
183 passed, 156 warnings in 17.53s
```

警告均为现有生命周期代码及测试中的 `datetime.utcnow()` 弃用提示；候选状态转换会放大条数，但无测试失败。未在本阻断修复中改变公共状态机时间语义。

## 提交边界

提交仅包含：

- `fast_t1_scan.py` 的价格带、真实生命周期指标和正式候选隔离相关精确 hunks；
- `strategies/full_market_t1.py` 生命周期适配与全局否决；
- `tests/test_fast_t1_scan.py` 主流程边界/真实字段回归；
- `tests/test_full_market_t1.py` 生命周期与全局否决回归。

用户既有 `load_or_compute_indicators`、两项指标缓存测试及其他脏工作树改动未提交，仍保留在工作区。

## 遗留关注

- 状态机仍使用 `datetime.utcnow()`，属于既有弃用警告；后续可独立迁移为 timezone-aware UTC，避免把时间语义改动混入本次交易决策阻断修复。

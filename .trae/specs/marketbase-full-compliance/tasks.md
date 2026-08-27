# Tasks

## 任务依赖关系

- Task 1 是基础，所有后续任务都依赖它
- Task 2 和 Task 5 可以并行
- Task 3 依赖 Task 2
- Task 4 依赖 Task 3
- Task 6 和 Task 7 可以并行
- Task 8 依赖 Task 1-7 全部完成

---

- [x] Task 1: A. 运行可靠性修复
  - [x] 1.1 修改 `_detect_session_slug` 移除 `intraday_morning`，仅返回 `intraday_1300`/`intraday_1400`/`intraday_1430`/`post_close`
  - [x] 1.2 修改 `run_collection` 中 `--phase` 参数校验，拒绝非法值
  - [x] 1.3 将 `.workflow.lock` 改为 JSON 格式，包含 pid/started_at/command/hostname
  - [x] 1.4 启动前检查锁 PID 是否真实存在，陈旧锁自动清理（阈值 30 分钟）
  - [x] 1.5 确保 try/finally 在正常/异常/中断后都清理锁（lock_handle.close() 前删除锁文件）
  - [x] 1.6 失败时在 run_dir 生成 `run_status.json` 和 `failure_reason.json`
  - [x] 1.7 确保 manifest.json 和 data_audit.json 使用临时文件+原子替换（已有 `_write_json_atomic`，确认覆盖）
  - [x] 1.8 强制所有输出 UTF-8：CSV 使用 `utf-8-sig`，日志使用 `utf-8`，终端输出设置 `PYTHONIOENCODING=utf-8`
  - [x] 1.9 为所有 HTTP 请求添加连接超时(10s)、读取超时(30s)、重试(3次)、指数退避，记录失败代码列表
  - [x] 1.10 分钟采集支持断点续跑：读取已完成的代码列表，跳过已成功采集的代码

- [x] Task 2: B. 全市场快照补齐
  - [x] 2.1 在 market_snapshot.csv 中补充 board、is_st、is_suspended、delist_risk、listed_days 字段
  - [x] 2.2 三市分别统计：在 data_audit.json 中记录 sh/sz/bj 的 declared/actual/unique/duplicate/missing 计数
  - [x] 2.3 逐列审计 quote_time、trade_date、source 及所有字段覆盖率
  - [x] 2.4 移除零成交/停牌/涨跌停的本地过滤逻辑，保留原始记录和客观标识
  - [x] 2.5 将原始 API 响应 JSON 文件、FIELDS.md 字段说明、SHA256 写入 run_dir
  - [x] 2.6 在 manifest.json 中记录所有文件的 SHA256

- [x] Task 3: C. 分钟数据修复
  - [x] 3.1 修改分钟数据输出为 `intraday_1m.parquet`（而非 intraday_minutes.parquet）
  - [x] 3.2 确保 parquet 包含 code/timestamp/open/high/low/close/volume/amount/cum_volume/cum_amount/source/fetched_at
  - [x] 3.3 修复 `_audit_intraday_minutes` 中 expected_minutes 计算：根据实际 phase 计算应有分钟数
  - [x] 3.4 审计每只股票分钟覆盖率、全市场每分钟覆盖率、缺失分钟、连续性断点、最后正常交易时间
  - [x] 3.5 实现 data_ready_static_only 判定：分钟数据不足（如 < 预期 20%）时明确标记
  - [x] 3.6 确保分钟采集失败不阻塞静态快照和审计落盘

- [x] Task 4: D. VWAP 与盘中客观结构
  - [x] 4.1 在 market_snapshot.csv 中直接写入 vwap/vwap_source/vwap_distance_pct/cum_volume/cum_amount/minute_sample_count
  - [x] 4.2 输出 distance_from_high_pct/distance_from_low_pct/amplitude_pct/volume_3m/volume_5m/amount_3m/amount_5m/amount_change_ratio
  - [x] 4.3 VWAP 不可用标记：量纲不可靠时 vwap=NaN, vwap_source="unavailable"
  - [x] 4.4 输出盘中指数快照：上证指数/沪深300/中证1000/创业板指/科创50 的价格/涨跌幅/高低/成交额/时间戳

- [x] Task 5: E. 日线与技术原料
  - [x] 5.1 确保 daily_indicators.csv 包含 MA5/10/20/60、MACD(DIF/DEA/Hist)、RSI14、BOLL(Upper/Middle/Lower)、ATR14/ATR14%、5/10/20日收益、20日高低、上下影线比例
  - [x] 5.2 每条日线注明 last_trade_date；盘中运行时说明是否含当日未收盘数据
  - [x] 5.3 审计并列出日线陈旧代码、短历史代码、失败代码，不混入"成功"计数

- [x] Task 6: F. 行业、概念与产业链分类
  - [x] 6.1 补齐缺失的约 396 只行业/概念映射，使覆盖率 100%
  - [x] 6.2 输出稳定的 classification_map.csv：code/industry/concepts/source/updated_at/coverage_status
  - [x] 6.3 创建 supply_chain_map.csv：theme/code/role/relation_type/evidence_source/updated_at
  - [x] 6.4 relation_type 区分 "official_disclosure" 和 "supply_chain_inference"
  - [x] 6.5 输出行业/概念客观聚合：component_count/advance_count/decline_count/advance_ratio/avg_change_pct/total_amount/avg_turnover/timestamp
  - [x] 6.6 确保本地仅做映射与聚合，不给出主题强度评分或选股结论

- [x] Task 7: G. 数据审计与交接契约
  - [x] 7.1 统一 data_audit.json 结构：quality_status/trade_date/session_phase/observed_at/coverage_gaps/duplicate_count/provider_errors/field_coverage/minute_continuity/daily_staleness/classification_coverage + quality_reason_codes
  - [x] 7.2 质量状态仅允许 data_ready/data_ready_static_only/partial/data_not_ready，附原因码
  - [x] 7.3 manifest.json 列出每个文件 name/rows/sha256/generated_at/relative_path
  - [x] 7.4 确保 run_dir 不可变（已有 `_create_run_directory` 逻辑，确认覆盖）
  - [x] 7.5 实现 latest_complete.json：只指向同时具备 manifest/audit 且质量合格的目录

- [x] Task 8: H. 性能与流程
  - [x] 8.1 确保静态快照优先完成并落盘（在分钟采集之前写入）
  - [x] 8.2 分钟采集记录每批耗时与失败原因到 batch_progress.json
  - [x] 8.3 确认日线/分类/分钟缓存去重跳过逻辑（已有 cache 机制，确认覆盖）
  - [x] 8.4 移除本地 Top200/local_reference_score/候选池/推荐输出
  - [x] 8.5 确保持仓/成本/仓位/交易决策不进入 Marketbase 输出

# Task Dependencies

- Task 1 是基础，所有后续任务都依赖它
- Task 2 和 Task 5 可以并行
- Task 3 依赖 Task 2
- Task 4 依赖 Task 3
- Task 6 和 Task 7 可以并行
- Task 8 依赖 Task 1-7 全部完成
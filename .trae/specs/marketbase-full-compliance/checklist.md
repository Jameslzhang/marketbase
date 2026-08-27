# Checklist

## A. 运行可靠性
- [x] `_detect_session_slug` 不再返回 `intraday_morning`，仅返回 `intraday_1300`/`intraday_1400`/`intraday_1430`/`post_close`
- [x] `--phase` 参数拒绝非法值（如 `13:00`），run_dir 目录名不包含 Windows 非法字符
- [x] 锁文件为 JSON 格式，包含 pid/started_at/command/hostname
- [x] 启动前检查 PID 是否存在，陈旧锁（>30min）自动清理
- [x] try/finally 保证正常/异常/中断后锁文件被清理
- [x] 失败时 run_dir 中存在 run_status.json 和 failure_reason.json
- [x] manifest.json 和 data_audit.json 使用临时文件原子替换
- [x] 所有日志/终端/CSV/JSON 强制 UTF-8，✓ 等 Unicode 字符正确输出
- [x] 所有 HTTP 请求有连接超时、读取超时、重试、指数退避、失败代码列表
- [x] 分钟采集支持断点续跑

## B. 全市场快照
- [x] market_snapshot.csv 包含 board/is_st/is_suspended/delist_risk/listed_days 字段
- [x] data_audit.json 中三市分别统计 declared/actual/unique/duplicate/missing
- [x] quote_time/trade_date/source 及所有字段逐列覆盖率审计
- [x] 零成交/停牌/涨跌停/一字板保留原始记录，不在本地删除或筛选
- [x] run_dir 包含原始 API 响应 JSON、FIELDS.md、SHA256
- [x] manifest.json 所有文件有 SHA256

## C. 分钟数据
- [x] run_dir 中存在 intraday_1m.parquet
- [x] parquet 含 code/timestamp/open/high/low/close/volume/amount/cum_volume/cum_amount/source/fetched_at
- [x] expected_minutes 根据实际 phase 计算（非固定 240）
- [x] 审计含每只股票覆盖率、全市场每分钟覆盖率、缺失分钟、连续性断点、最后交易时间
- [x] 分钟数据不足时标记为 data_ready_static_only（非模糊 "minute collected"）
- [x] 分钟采集失败不阻塞静态快照和审计落盘

## D. VWAP 与盘中结构
- [x] market_snapshot.csv 含 vwap/vwap_source/vwap_distance_pct/cum_volume/cum_amount/minute_sample_count
- [x] 含 distance_from_high_pct/distance_from_low_pct/amplitude_pct/volume_3m/volume_5m/amount_3m/amount_5m/amount_change_ratio
- [x] 量纲不可靠时 vwap=NaN, vwap_source="unavailable"
- [x] index_data.csv 含上证/沪深300/中证1000/创业板指/科创50 盘中快照

## E. 日线与技术原料
- [x] daily_indicators.csv 含 MA5/10/20/60/MACD/RSI14/BOLL/ATR/收益/高低/影线
- [x] 每条日线注明 last_trade_date 和是否含当日未收盘数据
- [x] 审计列出日线陈旧代码、短历史代码、失败代码（不混入成功计数）

## F. 行业分类
- [x] 分类覆盖率达到 100%（补齐约 396 只缺失映射）
- [x] classification_map.csv 含 code/industry/concepts/source/updated_at/coverage_status
- [x] supply_chain_map.csv 存在，relation_type 区分 official_disclosure 和 supply_chain_inference
- [x] industry_agg.csv 含 component_count/advance_count/decline_count/advance_ratio/avg_change_pct/total_amount/avg_turnover/timestamp
- [x] 无主题强度评分或选股结论

## G. 审计与交接契约
- [x] data_audit.json 结构统一，含 quality_reason_codes
- [x] quality_status 仅允许 data_ready/data_ready_static_only/partial/data_not_ready
- [x] manifest.json 含 name/rows/sha256/generated_at/relative_path
- [x] 每次运行生成独立不可变 run_dir
- [x] latest_complete.json 只指向质量合格的完整 run_dir

## H. 性能与流程
- [x] 静态快照在分钟采集之前落盘
- [x] 分钟采集记录 batch_progress.json（每批耗时/失败原因）
- [x] 缓存去重跳过逻辑完整
- [x] 无 Top200/local_reference_score/候选池/推荐输出
- [x] 无 position/cost/allocation/trade_decision 相关字段
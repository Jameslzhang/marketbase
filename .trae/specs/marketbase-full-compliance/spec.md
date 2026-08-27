# Marketbase 一次性补齐规范

## Why
当前 Marketbase 本地采集流水线存在多项可靠性、完整性和审计契约缺陷，需要一次性补齐以达成"本地只采集、计算客观字段、审计与存档"的完整目标。

## What Changes
- **A. 运行可靠性**：修复 phase 命名、文件锁、失败处理、原子写入、UTF-8 编码、采集超时重试等核心稳定性问题
- **B. 全市场快照**：补齐快照字段、审计三市覆盖率、原始响应归档、SHA256 校验
- **C. 分钟数据**：修复盘中分钟数计算、输出 parquet、审计覆盖率与连续性、失败不阻塞
- **D. VWAP 与盘中结构**：快照中直接写入 VWAP、距高低、振幅、分钟量价结构、盘中指数快照
- **E. 日线与技术原料**：补齐技术指标字段、审计陈旧/短历史代码、复权口径说明
- **F. 行业分类**：补齐约 396 只缺失映射、输出 stable classification_map.csv、supply_chain_map.csv
- **G. 审计与交接契约**：固定 data_audit.json 结构、manifest.json 完整性、latest_complete.json 指向
- **H. 性能与流程**：快照优先、分钟可恢复、去重跳过、禁止本地推荐输出

## Impact
- Affected specs: 全流程 (all pipeline steps)
- Affected code: `local_workflow.py`, `marketbase/pipeline/*`, `marketbase/live_workflow.py`, `marketbase/intraday_collector.py`, `marketbase/minute_collector.py`, `marketbase/data_audit.py`, `marketbase/classification_collector.py`, `marketbase/snapshot.py`, `marketbase/daily_collector.py`, `marketbase/indicators.py`

---

## ADDED Requirements

### Requirement: Phase 合法标识
系统 SHALL 只接受 `--phase` 值为 `intraday_1300`、`intraday_1400`、`post_close`（自动检测时也仅生成这些值）。

#### Scenario: 传入非法 phase 被拒绝
- **WHEN** 用户传入 `--phase "13:00"` 或 `--phase "intraday_morning"`
- **THEN** 系统返回错误，提示合法 phase 值

#### Scenario: 自动检测 phase 仅生成合法值
- **WHEN** 系统在 13:00-14:00 之间自动检测
- **THEN** session_phase 为 `intraday_1300`（非 `intraday_morning`）

### Requirement: JSON 文件锁
系统 SHALL 使用 JSON 格式的锁文件，包含 `pid`、`started_at`、`command`、`hostname` 字段。

#### Scenario: 锁文件格式
- **WHEN** 采集进程启动并获取锁
- **THEN** 锁文件为 JSON 格式，包含 pid/started_at/command/hostname

### Requirement: 陈旧锁自动清理
系统 SHALL 在启动时检查锁文件中的 PID 是否真实存在，若进程不存在且锁超过阈值（默认 30 分钟），自动清理陈旧锁。

#### Scenario: 陈旧锁被清理
- **WHEN** 锁文件中的 PID 已不存在且锁超过 30 分钟
- **THEN** 系统自动删除陈旧锁并继续运行

#### Scenario: 活跃锁阻止重复运行
- **WHEN** 锁文件中的 PID 存在且进程运行中
- **THEN** 系统退出并提示"采集已在运行中"

### Requirement: try/finally 锁清理
系统 SHALL 使用 try/finally 保证正常退出、异常退出、被中断后都清理锁文件。

#### Scenario: 异常退出后锁被清理
- **WHEN** 采集过程中抛出未捕获异常
- **THEN** finally 块中锁文件被清理

### Requirement: 失败时生成 run_status.json 和 failure_reason.json
系统 SHALL 在采集失败时在 run_dir 中生成 `run_status.json`（状态=failed）和 `failure_reason.json`（包含错误类型、消息、堆栈、时间戳）。

#### Scenario: 异常失败时生成状态文件
- **WHEN** 采集过程中抛出异常
- **THEN** run_dir 中存在 run_status.json 和 failure_reason.json

### Requirement: manifest.json 和 data_audit.json 原子写入
系统 SHALL 使用临时文件写完后再原子替换的方式写入 manifest.json 和 data_audit.json，确保未完成的 run 不被误判为可用。

#### Scenario: 写入中断不产生半成品
- **WHEN** manifest.json 写入过程中系统崩溃
- **THEN** run_dir 中不存在残缺的 manifest.json（仅有临时文件残留）

### Requirement: 强制 UTF-8 编码
系统 SHALL 强制所有日志、终端输出、CSV、JSON 使用 UTF-8 编码。CSV 使用 UTF-8-BOM 以兼容 Excel。

#### Scenario: 特殊字符正确输出
- **WHEN** 终端输出包含 ✓ 等 Unicode 字符
- **THEN** 终端正确显示 ✓（不再因 GBK 编码失败导致异常）

### Requirement: 采集请求超时与重试
系统 SHALL 为所有 HTTP 采集请求配置连接超时（默认 10s）、读取超时（默认 30s）、重试次数（默认 3 次）、指数退避（base=1s, factor=2），并记录失败代码列表。

#### Scenario: 单次请求超时后重试
- **WHEN** 某批次请求连接超时
- **THEN** 系统等待退避时间后重试，最多 3 次

#### Scenario: 断点续跑
- **WHEN** 分钟采集中断后重新运行
- **THEN** 系统跳过已成功采集的代码，仅补采失败的代码

---

## MODIFIED Requirements

### Requirement: 全市场快照字段
系统 SHALL 在 market_snapshot.csv 中每行至少输出：code、name、market、board、is_st、is_suspended、delist_risk、listed_days、price、pre_close、open、high、low、change_pct、volume、amount、turnover_rate、volume_ratio、total_mv、circ_mv、pe_ratio、pb_ratio、quote_time、source。

**Changes**: 新增 board、is_st、is_suspended、delist_risk、listed_days 字段；保留原有字段。

#### Scenario: 快照包含完整字段
- **WHEN** 采集完成
- **THEN** market_snapshot.csv 包含上述所有字段列

### Requirement: 三市分别统计
系统 SHALL 在审计中分别统计沪市、深市、北交所的声明总数、实际行数、唯一代码数、重复数、缺失数。

#### Scenario: 三市审计
- **WHEN** 采集完成
- **THEN** data_audit.json 中 market_counts 包含 sh/sz/bj 分别的 declared/actual/unique/duplicate/missing 计数

### Requirement: 逐列字段覆盖率审计
系统 SHALL 对 quote_time、trade_date、source 及所有数据字段进行逐列覆盖率审计，记录非空比例。

#### Scenario: 字段覆盖率审计
- **WHEN** 采集完成
- **THEN** data_audit.json 包含 field_coverage 对象，key 为字段名，value 为覆盖率

### Requirement: 零成交/停牌/涨跌停保留原始记录
系统 SHALL 保留零成交、停牌、涨跌停、一字板等股票的原始记录和客观标识，不在本地删除或筛选。

#### Scenario: 零成交股票保留
- **WHEN** 某股票成交量为 0
- **THEN** 该股票记录仍写入 market_snapshot.csv，is_suspended 或 zero_volume 标记为 true

### Requirement: 原始响应归档
系统 SHALL 将每次 run 的 market_snapshot.csv、market_snapshot.json、原始 API 响应文件、字段说明、SHA256 都放入 run_dir。

#### Scenario: run_dir 内容完整
- **WHEN** 采集完成
- **THEN** run_dir 包含 market_snapshot.csv/json、原始响应 JSON、FIELDS.md、所有文件的 SHA256 在 manifest.json 中

### Requirement: 分钟数据输出 parquet
系统 SHALL 将本次 run 专属的分钟数据输出为 `intraday_1m.parquet`，放入 run_dir，不能仅依赖缓存。

#### Scenario: run_dir 包含分钟 parquet
- **WHEN** 盘中时段采集完成
- **THEN** run_dir 中存在 intraday_1m.parquet

### Requirement: 分钟数据字段
系统 SHALL 在 intraday_1m.parquet 中包含字段：code、timestamp、open、high、low、close、volume、amount、cum_volume、cum_amount、source、fetched_at。

#### Scenario: 分钟数据字段完整
- **WHEN** 分钟采集完成
- **THEN** parquet 包含上述所有列

### Requirement: 盘中阶段实际分钟数计算
系统 SHALL 根据实际采集阶段计算应有分钟数：
- `intraday_1300`：上午全时段 + 13:00 至快照时点
- `intraday_1400`：上午全时段 + 13:00 至 14:00
- `post_close`：全天 240 分钟

不再错误固定为全天 240 分钟。

#### Scenario: 13:00 阶段分钟数
- **WHEN** 当前阶段为 intraday_1300，快照时点为 13:25
- **THEN** expected_minutes = 上午 120 + 13:00~13:25 = 145 分钟

### Requirement: 分钟覆盖率审计
系统 SHALL 审计每只股票的分钟覆盖率、全市场每分钟覆盖率、缺失分钟、连续性断点、最后正常交易时间。

#### Scenario: 分钟审计明细
- **WHEN** 分钟采集完成
- **THEN** data_audit.json 中 intraday_minutes 包含 code_coverage（每只股票分钟数/覆盖率）、market_minute_coverage（每分钟覆盖率）、continuity_breaks

### Requirement: data_ready_static_only 状态
系统 SHALL 在分钟数据不足（如只采到 3-7 个时点）时明确标记为 `data_ready_static_only`，不得标记为 `data_ready` 或模糊的 "minute collected"。

#### Scenario: 极少分钟数据
- **WHEN** 分钟采集仅获得 5 个时点的数据
- **THEN** quality_status 为 `data_ready_static_only`

### Requirement: 分钟采集失败不阻塞
系统 SHALL 确保分钟采集失败不阻塞静态快照和审计落盘。

#### Scenario: 分钟采集异常
- **WHEN** 分钟采集过程中抛出异常
- **THEN** 静态快照、日线、审计文件正常写入，分钟审计标记为 failed

### Requirement: VWAP 与盘中结构字段
系统 SHALL 在 market_snapshot.csv 直接写入：vwap、vwap_source、vwap_distance_pct、cum_volume、cum_amount、minute_sample_count。

#### Scenario: VWAP 字段写入
- **WHEN** 分钟数据可用
- **THEN** market_snapshot.csv 包含 vwap 列（基于分钟数据计算）

### Requirement: 盘中价格结构
系统 SHALL 输出：distance_from_high_pct、distance_from_low_pct、amplitude_pct、volume_3m、volume_5m、amount_3m、amount_5m、amount_change_ratio。

#### Scenario: 盘中价格结构
- **WHEN** 分钟数据可用
- **THEN** market_snapshot.csv 包含上述盘中结构字段

### Requirement: VWAP 不可用标记
系统 SHALL 在量纲或数据源不可靠时将 VWAP 标记为不可用（vwap=NaN, vwap_source="unavailable"），不得静默计算错误值。

#### Scenario: 数据源不可靠
- **WHEN** 分钟数据源返回错误或量纲不一致
- **THEN** vwap 为 NaN，vwap_source 为 "unavailable"

### Requirement: 盘中指数快照
系统 SHALL 输出盘中指数快照：上证指数(000001)、沪深300(000300)、中证1000(000852)、创业板指(399006)、科创50(000688) 的价格、涨跌幅、高低、成交额、时间戳。

#### Scenario: 指数快照输出
- **WHEN** 采集完成
- **THEN** index_data.csv 包含上述 5 个指数的盘中快照行

### Requirement: 日线技术指标
系统 SHALL 输出客观计算字段：MA5/10/20/60、MACD(DIF/DEA/Histogram)、RSI14、BOLL(Upper/Middle/Lower)、ATR14/ATR14%、5/10/20日收益、20日高低、上下影线比例。

#### Scenario: 技术指标完整
- **WHEN** 日线采集完成
- **THEN** daily_indicators.csv 包含上述所有列

### Requirement: 日线 last_trade_date 标注
系统 SHALL 在每条日线记录中注明 last_trade_date；盘中日线指标必须说明是否含当日未收盘数据。

#### Scenario: 盘中日线标注
- **WHEN** 盘中运行采集
- **THEN** daily_indicators.csv 中 last_trade_date 为前一日，且 includes_intraday_today 为 false

### Requirement: 日线陈旧/短历史审计
系统 SHALL 审计并列出日线陈旧代码（last_trade_date 非当日）、短历史代码（行数不足）、失败代码，不把陈旧数据与当日数据混为"成功"。

#### Scenario: 陈旧日线分离
- **WHEN** 某股票最新日线日期为 T-3
- **THEN** 该股票出现在 stale_daily.stale_codes 中，daily_success 不包含它

### Requirement: 行业分类补齐
系统 SHALL 补齐当前缺失的约 396 只行业/概念映射，使分类覆盖率达到 100%。

#### Scenario: 分类全覆盖
- **WHEN** 分类采集完成
- **THEN** classification_map.csv 覆盖所有快照中的股票代码

### Requirement: classification_map.csv 稳定输出
系统 SHALL 输出稳定的 classification_map.csv：包含 code、industry、concepts、source、updated_at、coverage_status 字段。

#### Scenario: 分类映射稳定
- **WHEN** 多次采集
- **THEN** 同一股票的行业/概念映射保持一致（除非官方更新）

### Requirement: supply_chain_map.csv
系统 SHALL 单独维护 supply_chain_map.csv：包含 theme、code、role、relation_type、evidence_source、updated_at 字段。
relation_type 必须区分 "official_disclosure"（官方披露直接关系）和 "supply_chain_inference"（产业链推断）。

#### Scenario: 产业链关系类型
- **WHEN** 某股票与新能源汽车产业链关联
- **THEN** relation_type 为 "official_disclosure" 或 "supply_chain_inference"，不可混写

### Requirement: 行业客观聚合
系统 SHALL 输出行业/概念客观聚合：成分数、上涨数、下跌数、上涨比例、平均涨幅、成交额、换手、时间戳。不给出主题强度评分或选股结论。

#### Scenario: 行业聚合
- **WHEN** 采集完成
- **THEN** industry_agg.csv 包含 component_count/advance_count/decline_count/advance_ratio/avg_change_pct/total_amount/avg_turnover/timestamp

### Requirement: data_audit.json 固定结构
系统 SHALL 在 data_audit.json 中固定包含：quality_status、trade_date、session_phase、observed_at、coverage_gaps、duplicate_count、provider_errors、field_coverage、minute_continuity、daily_staleness、classification_coverage。

#### Scenario: 审计结构完整
- **WHEN** 采集完成
- **THEN** data_audit.json 包含上述所有顶级字段

### Requirement: 质量状态枚举
系统 SHALL 仅允许以下质量状态：`data_ready`、`data_ready_static_only`、`partial`、`data_not_ready`。每个状态附机器可读原因码。

#### Scenario: 质量状态含原因码
- **WHEN** quality_status 为 data_ready_static_only
- **THEN** data_audit.json 包含 quality_reason_codes 数组，如 ["minute_insufficient", "minute_coverage_below_threshold"]

### Requirement: manifest.json 文件清单
系统 SHALL 在 manifest.json 中列出每个输出文件的行数、SHA256 校验值、生成时间、相对路径。

#### Scenario: manifest 完整性
- **WHEN** 采集完成
- **THEN** manifest.json 中 files 对象包含每个文件的 name/rows/sha256/generated_at/relative_path

### Requirement: 不可变 run_dir
系统 SHALL 每次运行保存不可变 run_dir，不得覆盖旧快照。

#### Scenario: 多次运行不覆盖
- **WHEN** 同一天多次运行采集
- **THEN** 每次生成独立的 run_dir（如 `130000_intraday_1300_objective_data`、`130001_intraday_1300_objective_data_2`）

### Requirement: latest_complete.json
系统 SHALL 维护 `latest_complete.json`，只指向同时具备 manifest、audit 且质量合格（data_ready 或 data_ready_static_only）的最新 run_dir，避免误读半成品。

#### Scenario: 半成品不被指向
- **WHEN** 某次 run 失败（无 manifest 或 quality_status 为 data_not_ready）
- **THEN** latest_complete.json 不指向该 run_dir

### Requirement: 快照优先落盘
系统 SHALL 优先完成静态全市场快照并落盘，目标 13:00 后尽快可用。

#### Scenario: 快照优先
- **WHEN** 盘中运行采集
- **THEN** market_snapshot.csv 和 data_audit.json（静态部分）在分钟采集之前已经写入 run_dir

### Requirement: 分钟可恢复批次
系统 SHALL 采用可恢复批次采集分钟数据，记录每批耗时与失败原因，支持断点续跑。

#### Scenario: 批次记录
- **WHEN** 分钟采集分批进行
- **THEN** 每批完成后记录耗时和失败代码到 batch_progress.json

### Requirement: 去重跳过
系统 SHALL 同一运行不重复拉取已完成且校验通过的日线、分类和原始分钟分区。

#### Scenario: 重复运行跳过
- **WHEN** 日线缓存已存在且校验通过
- **THEN** 不重新拉取

### Requirement: 禁止本地推荐输出
系统 SHALL 不在本地生成 Top200、local_reference_score、本地候选池或本地推荐。

#### Scenario: 无推荐输出
- **WHEN** 采集完成
- **THEN** run_dir 中无任何 top200、score、candidate、recommendation 文件

### Requirement: 持仓/成本/仓位/交易决策分离
系统 SHALL 将持仓、成本、仓位和交易决策排除在 Marketbase 输出之外，由分析层单独管理。

#### Scenario: 无交易数据
- **WHEN** 采集完成
- **THEN** 输出文件中无 position、cost、allocation、trade_decision 相关字段
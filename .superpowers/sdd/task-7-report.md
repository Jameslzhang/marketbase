# 任务 7 报告

## 范围

- 重写 `local_workflow.py`，仅保留客观数据采集与 `fulfill-request`。
- 重写 `tests/test_local_workflow.py`，测试全部使用离线三市场和三代码 fixture。
- 更新 `.vscode/launch.json` 为 `MarketBase: 一键客观数据采集`，无策略参数。
- 未修改任务 1-6 的实现，未执行 Git 提交。

## RED / GREEN

RED：删除旧入口后先运行：

```text
python -m pytest tests/test_local_workflow.py -q
ModuleNotFoundError: No module named 'local_workflow'
```

GREEN：实现最小协议后，补齐注入市场采集器的缓存写入与无子命令 CLI 边界；再为未注入分钟 fetcher 的默认行为新增 RED 测试并修复。

```text
9 passed in 1.57s
All checks passed!
```

## 输出结构

每次采集生成：

```text
<data_root>/<YYYY-MM-DD>/<HHMMSS>_objective_data/
  market_snapshot.csv
  market_snapshot.json
  daily_indicators.csv
  classification_map.csv
  data_audit.json
  manifest.json
  workflow.log
```

缓存、持久化分类映射与最新交接位于运行目录外：

```text
<data_root>/cache/market_snapshot.json
<data_root>/cache/daily/<code>.json
<data_root>/cache/daily_checkpoint.json
<data_root>/classification_map.csv
<data_root>/latest_codex_input.json
```

`manifest.json` 使用 schema v1，记录运行目录、运行产物名称、行数、SHA-256、缓存路径与错误计数；`latest_codex_input.json` 原子更新到本次运行的绝对路径和审计内容。

## 验证

```text
.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -q
9 passed

.venv\Scripts\python.exe -m ruff check local_workflow.py tests\test_local_workflow.py
All checks passed!

.venv\Scripts\python.exe -m pytest \
  tests\test_neutral_indicators.py tests\test_data_request.py \
  tests\test_daily_collector.py tests\test_daily.py \
  tests\test_market_collector.py tests\test_data_audit.py \
  tests\test_live_workflow.py tests\test_snapshot.py \
  tests\test_classification_map.py tests\test_minute_collector.py -q
155 passed
```

## 审查修复（2026-07-22）

### RED / GREEN

新增 7 项回归测试后，首轮定向执行得到 6 个预期失败：日线进度字段不完整、逐代码缓存审计缺失、`workflow.log` 行数为 0、混合大小写英文禁词未过滤、目录碰撞未重试、较早运行覆盖较新 `latest`。随后新增一项 provider 文本落盘中性化测试，先预期失败，再在 JSON/CSV 写入边界修复。

GREEN：

```text
.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -q
16 passed in 3.95s

.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py tests\test_data_audit.py tests\test_daily_collector.py tests\test_market_collector.py tests\test_classification_map.py tests\test_data_request.py tests\test_minute_collector.py -q
89 passed in 3.22s

.venv\Scripts\python.exe -m ruff check marketbase\neutral_indicators.py marketbase\data_audit.py marketbase\daily_collector.py marketbase\market_collector.py marketbase\classification_map.py marketbase\data_request.py marketbase\minute_collector.py local_workflow.py tests\test_local_workflow.py
All checks passed!
```

### 输出结构

`workflow.log` 的每个日线事件包含带时区的前缀时间及 `wall_time`、`elapsed`、`rate`、`eta`、`completed`、缓存命中、失败、待处理、当前代码、当前来源和中性化后的最后错误。日志完成后才生成 manifest，因此 manifest 记录最终 `workflow.log` 的真实行数和 SHA-256；manifest 不记录自身，避免自引用。

`data_audit.json` 与 `latest_codex_input.json.audit.daily` 现在包含 `cache_coverage_count`、`cache_coverage_rate`、`short_history`（仅以 `actual_rows < 250` 标记，原因固定为 `short_history`）、`latest_date_distribution`、`source_counts` 和 `invalid_or_missing_cache`。扫描范围是本次请求的全部代码缓存。

### 残余风险

latest 发布在替换前读取当前 `generated_at`，只有新值不早于已有值才使用原子 replace。该策略防止通常的较早运行覆盖已发布的较新运行；跨进程同时完成且两者都在读取后写入的极窄竞态仍需要文件锁或外部协调才能彻底消除。

额外确认：`--help` 仅列出 `collect` 和 `fulfill-request`；入口源码未导入旧候选或策略模块，也未包含禁止的旧限制字段。

## 残余风险

- 默认无参采集会调用真实外部数据源；本任务的全部测试通过 provider 注入离线执行，未进行真实全市场请求。
- 数据源返回的原始名称和事实字段由下游采集模块规范化；入口仅负责协议编排、持久化与中性错误摘要。
### 最终并发与键名修复

- JSON 键和 CSV 列名现在与值使用同一套中性化规则；中性化后重名会追加序号，避免静默覆盖。
- latest 发布使用进程内锁和 Windows 文件区间锁，将“读取现有时间、比较、原子替换”放入同一临界区，保证并发发布时较新的 `generated_at` 最终胜出。

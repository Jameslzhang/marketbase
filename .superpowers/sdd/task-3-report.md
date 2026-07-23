# 任务 3 实施报告

## 实现摘要

- 新增可恢复的全市场日线采集器 `marketbase.daily_collector`。
- 提供冻结的 `DailyProgressEvent`、`DailyCollectionReport`、
  `collect_daily_universe` 和 `read_daily_cache` 公共接口。
- 采集器按输入顺序串行调用 fetcher，校验唯一六位数字代码和 1..250 的
  lookback；单只失败会记录并继续处理其他代码。
- 每只代码处理后原子写入断点；有效同日缓存优先于断点，损坏、缺失、跨日或
  lookback 不一致的缓存会重新抓取。
- 缓存统一为七列升序 JSON 行记录，保留实际行数与数据来源；缓存和断点均通过
  同目录唯一临时文件加 `Path.replace()` 写入。

## RED / GREEN

- RED 1：新增测试后运行 `pytest tests\\test_daily_collector.py -q`，因
  `marketbase.daily_collector` 不存在而在收集阶段报 `ModuleNotFoundError`。
- GREEN 1：实现最小采集器后，新增测试 13 项通过。
- RED 2：补充乱序缓存、中立错误文本和非整数 lookback 用例后，测试按预期暴露
  三项缺口：乱序缓存被误命中、错误文本未清理、字符串 lookback 抛出 TypeError。
- GREEN 2：收紧缓存结构验证、加入中立错误清理和 lookback 类型检查后，新增测试
  15 项通过。

## 变更文件

- `marketbase/daily_collector.py`（新增）
- `tests/test_daily_collector.py`（新增）
- `.superpowers/sdd/task-3-report.md`（新增，本报告）
- `marketbase/daily.py` 未修改。

## 验证结果

```text
.\\.venv\\Scripts\\python.exe -m pytest tests\\test_daily_collector.py tests\\test_daily.py -q
46 passed in 1.27s

.\\.venv\\Scripts\\python.exe -m ruff check marketbase\\daily_collector.py marketbase\\daily.py tests\\test_daily_collector.py
All checks passed!
```

### 最终复核修复

- 新增空 `daily_source` 回归测试；上游未标明实际来源时按逐代码失败处理，不写入立即失效的缓存，也不计入成功与来源统计。
- 保持其他代码继续处理，并在 checkpoint 的 `failed_codes` 中记录该代码供下次重试。

## 自检与残余风险

- 已覆盖首次写入、断点失败重试、断点与缓存不一致、同日命中、跨日/损坏/乱序/
  超限缓存重抓、短历史、失败隔离、来源和错误统计、进度、输入校验及原子写入失败。
- 未调用真实外部日线数据源；其网络可用性与字段变化仍由现有 `fetch_daily_history`
  的多来源逻辑负责。采集器会将该类单代码错误隔离并记录。
- 原子写入测试模拟了 `Path.replace()` 失败并确认旧文件与临时文件清理；不同文件
  系统在断电时的持久化语义不在本地单元测试范围内。

## 审查修复记录（2026-07-22）

### RED / GREEN

- RED：先补充运行时钟不冻结、缓存严格元数据和畸形 checkpoint 重建测试；现状下
  `tests/test_daily_collector.py` 出现 11 个预期失败，分别暴露三类审查问题。
- GREEN：引入可 monkeypatch 的内部 `_current_time()`，并让缓存写入、checkpoint 更新、
  progress `wall_time` 和报告 `finished_at` 各自读取当时的带时区时间；`now` 仅用于交易日
  与 `started_at` 基准。补充嵌套 JSON 列表 checkpoint 用例后，确认畸形状态不会触发
  `TypeError`，而是按空状态重建。

### 修改与考量

- `marketbase/daily_collector.py`：严格要求 cache `schema_version` 为非 bool 的 int 1、
  `fetched_at` 为带时区 ISO、`source` 非空、`source_errors` 为字符串列表；checkpoint
  的 `updated_at` 同样要求带时区 ISO，并验证 completed/failed 为当前代码集合内的唯一
  代码且互斥，任一结构异常都回退为空状态。
- `tests/test_daily_collector.py`：新增上述审查回归测试，并更新原有首写入断言以适配
  不再冻结的 `fetched_at` 语义。
- `.superpowers/sdd/task-3-report.md`：追加本修复记录。未修改 `marketbase/daily.py`
  或其他范围外文件；未执行 Git 提交。

### 最终验证

```text
.\\.venv\\Scripts\\python.exe -m pytest tests\\test_daily_collector.py tests\\test_daily.py -q
59 passed in 3.67s

.\\.venv\\Scripts\\python.exe -m ruff check marketbase\\daily_collector.py marketbase\\daily.py tests\\test_daily_collector.py
All checks passed!
```

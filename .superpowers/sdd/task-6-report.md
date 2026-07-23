# 任务 6 报告

## 范围

- 新增 `marketbase/minute_collector.py`。
- 新增 `tests/test_minute_collector.py`。
- 未修改 `marketbase/data_request.py` 或 `marketbase/live_workflow.py`：现有冻结请求、严格当日校验和腾讯分钟拉取接口可直接复用。
- 未提交 Git。

## RED/GREEN

1. 先创建任务专属测试。首次运行因 `marketbase.minute_collector` 不存在而在导入阶段失败，确认测试覆盖的是缺失的公开模块。
2. 实现分钟行解析、区间裁剪、日线缓存读取/缺失补取、指标分组、协议装配以及逐 code/scope 错误隔离；随后 `tests/test_minute_collector.py` 全部通过。
3. 补充审计行数断言，验证审计记录实际处理的日线和分钟区间行数，而非仅记录输出 `raw` 行数；该测试先失败，再调整实现后通过。

## 覆盖行为

- 分钟行格式过滤、时间排序、重复分钟保留最后一行与闭区间裁剪。
- 当日分钟防御校验、空区间 code 级错误、请求字段白名单和累计金额/成交量 VWAP。
- 日线缓存命中、尾部 lookback、缓存缺失时仅补取请求代码并以既有 schema 原子写回。
- 中性 MA/RSI/MACD/ATR 分组输出、短历史不伪造行。
- 日线与分钟、不同代码之间的错误隔离；错误文本去除禁用候选/推荐/交易/信号类词汇。
- 响应顶层字段顺序、请求代码顺序、时区时间戳和 JSON 可序列化性。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_minute_collector.py tests\test_data_request.py tests\test_daily_collector.py tests\test_neutral_indicators.py -q
# 69 passed in 1.70s

.\.venv\Scripts\python.exe -m ruff check marketbase\minute_collector.py marketbase\data_request.py tests\test_minute_collector.py
# All checks passed!

git diff --check
# exit 0; 仅报告既有工作区文件的 CRLF 警告
```

## 剩余风险

- 测试使用注入式获取器，未对腾讯和日线外部服务执行网络冒烟测试；运行时仍可能受服务可用性和上游字段变化影响。
- 新模块复用 `daily_collector` 的缓存标准化和原子写入内部 helper；相关缓存协议若在未来变更，应同步复核本模块。
## 审查修复

- 日线缓存命中前同时校验元数据 `code` 与请求代码，错码缓存会被拒绝并仅补取当前请求代码。
- 响应返回前递归将非有限浮点数转换为 `null`，并用 `json.dumps(..., allow_nan=False)` 验证严格 JSON 可序列化。

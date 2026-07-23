### 任务 6：当前交易日分钟数据与 Codex 机械补数响应

在 `D:\Environment\marketbase-main` 中实现只按已验证 `DataRequest` 补数据的响应组装器。

**文件范围**
- 新建 `marketbase/minute_collector.py`
- 新建 `tests/test_minute_collector.py`
- 仅确有必要时修改 `marketbase/data_request.py`、`marketbase/live_workflow.py` 及对应测试
- 不修改入口，不提交 Git。

**先复用**
- `marketbase.data_request` 的 frozen dataclass 和严格请求校验
- `marketbase.live_workflow.fetch_tencent_minute_rows`
- `marketbase.daily_collector.read_daily_cache` 与全量 `<daily_cache_root>/<code>.json`
- `marketbase.daily.fetch_daily_history` 只用于明确请求代码的缓存缺失补取
- `marketbase.neutral_indicators.compute_daily_indicators` 与 `compute_vwap`

**公开接口**
- `parse_minute_rows(rows: Iterable[str]) -> pd.DataFrame`，固定列 `time,price,volume,amount`
- `slice_minute_interval(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame`
- `collect_requested_data(request: DataRequest, *, daily_cache_root: str|Path, minute_fetcher=fetch_tencent_minute_rows, daily_fetcher=fetch_daily_history, now: datetime|None=None) -> dict[str, object]`

**分钟语义**
- 腾讯行格式按当前实现解析：`HHMM price cumulative_volume cumulative_amount`；标准化 time 为 `HH:MM`，数值字段为 float。
- 丢弃格式错误、非法时间、非数值、负 volume/amount 行；按 time 稳定排序并对重复分钟保留最后一条。
- 只允许 `request.minute.date == now` 所在本地日期（DataRequest 通常已验证，仍做防御检查）。
- 区间 start/end 含边界；空区间不是整个请求失败，代码级 minute error 记录 `no minute rows in requested interval`。
- `raw` 保存请求区间标准化原始累计行；`vwap` 使用区间最后一行累计 amount / (volume*100)，volume=0 时为 null。不得输出 `above_vwap/hold/activity/signal` 等解释字段。
- 分钟响应只包含 request.minute.fields 明确请求的 `raw`、`vwap`。

**日线语义**
- 优先读取 `<daily_cache_root>/<code>.json`；有效 250 日缓存可满足较短 lookback，尾部裁剪，不要求 requested_lookback 与请求完全相同。
- 缓存缺失/损坏/不足时，只为请求代码调用 `daily_fetcher(code, lookback_days=max(request.lookback,250), source="auto", retries=2)`；若现有 fetcher 上限语义要求 250，则固定 250，并将标准化结果原子写回与 Task 3 相同 cache schema。
- 不伪造上市不足 lookback 的行；实际短历史仍成功并记录 actual rows。
- daily 响应只包含 request.daily.fields：`raw` 为尾部 lookback 行；`ma/rsi/macd/atr` 从客观指标结果分组输出，缺少足够历史时为 null。
- 若复用 Task 3 写缓存较复杂，可新增内部 helper，但必须保持缓存协议兼容且原子写。

**响应协议**
- 顶层固定：`schema_version=1,request_id,generated_at,data,errors,audit`。
- `generated_at` 为带时区 ISO；`data` 以请求 codes 原顺序插入，每代码可有 `daily`/`minute`。
- `errors` 是对象/列表，必须能区分 `code,scope,source,error,observed_at`；一只代码失败不得影响其他代码，daily 失败不得阻断同代码 minute，反之亦然。
- `audit` 至少含 requested/success/failed 的代码或 scope 计数、daily/minute 行数和来源计数。
- 所有错误中性化，禁止中英文候选、推荐、买卖、信号、评分、排名、概率文本。
- 不筛选、不排序股票、不解释指标、不添加交易字段。

**TDD 测试**
- RED：模块不存在。
- 分钟解析、非法行、排序、重复分钟、含边界裁剪。
- 09:31-09:32 只返回两行且 VWAP 数学正确。
- 仅返回明确请求字段，无解释字段。
- 当日防御校验与空区间代码级错误。
- 日线缓存命中、尾部 lookback、requested fields 分组、中性指标。
- 缓存缺失只补请求代码并写回兼容缓存。
- 日线/分钟逐 scope 失败隔离，多代码失败隔离，错误中性化。
- 请求代码顺序与响应 JSON 可序列化。

**验证**
- `.\.venv\Scripts\python.exe -m pytest tests\test_minute_collector.py tests\test_data_request.py tests\test_daily_collector.py tests\test_neutral_indicators.py -q`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\minute_collector.py marketbase\data_request.py tests\test_minute_collector.py`

完成后写 `.superpowers\sdd\task-6-report.md`，包含 RED/GREEN、文件、测试、Ruff 与残余风险。

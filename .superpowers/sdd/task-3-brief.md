### 任务 3：全量日线缓存、限流和断点恢复

在 `D:\Environment\marketbase-main` 中实现可恢复的全市场 250 日日线采集器。

**文件范围**
- 新建 `marketbase/daily_collector.py`
- 新建 `tests/test_daily_collector.py`
- 只有确有必要时才修改 `marketbase/daily.py`，不得改动其他文件。

**公开接口**
- 冻结 dataclass `DailyProgressEvent`：`completed,total,success_count,cache_hit_count,failure_count,current_code,current_source,wall_time,elapsed_seconds,rate_per_minute,eta_seconds,last_error`。
- 冻结 dataclass `DailyCollectionReport`：`trading_date,total_count,success_count,cache_hit_count,failure_count,pending_count,source_counts,errors,started_at,finished_at,elapsed_seconds,checkpoint_path,cache_root`。
- `collect_daily_universe(codes: Iterable[str], *, cache_root: str|Path, checkpoint_path: str|Path, lookback: int=250, fetcher: Callable=fetch_daily_history, progress: Callable[[DailyProgressEvent],None]|None=None, now: datetime|None=None) -> DailyCollectionReport`
- `read_daily_cache(path: str|Path) -> tuple[pd.DataFrame, dict[str, object]]`

**输入校验**
- 代码必须为唯一六位数字；重复或非法代码在任何 fetcher 调用前 `ValueError`。
- `lookback` 固定允许 1..250；本地默认调用 250。
- 保持输入代码顺序。

**缓存协议**
- 路径：`<cache_root>/<code>.json`。
- JSON：`schema_version=1, code, fetched_at, trading_date, requested_lookback, actual_rows, latest_date, source, source_errors, rows`。
- `rows` 为标准化原始日线记录，列固定为 `date,open,high,low,close,volume,amount`，最多 250 行，日期升序。
- 写入使用同目录唯一临时文件 + `Path.replace()`；失败不得破坏旧缓存。
- 同一 `trading_date`、相同 `requested_lookback`、非空且结构合法的缓存计为 cache hit，不调用 fetcher。
- 上市不足 250 日仍为成功，`actual_rows` 保存实际数量；不得伪造行。

**fetcher 调用**
- 调用形式：`fetcher(code, lookback_days=lookback, source="auto", retries=2)`。
- 从返回 DataFrame `attrs` 读取 `daily_source` 和 `source_errors`。
- 标准化中英文日线列；无有效 close 或空结果视为逐代码失败。
- 一只失败不得中断其他代码。
- 默认顺序处理以降低限流风险；本任务不增加并发。后续若优化并发必须保持相同接口。

**断点协议**
- JSON：`schema_version=1,trading_date,requested_lookback,total_codes,completed_codes,failed_codes,updated_at`。
- 每处理一只（成功、缓存命中或失败）立即原子更新。
- 同日重启时：有效缓存优先于断点；断点标记完成但缓存缺失/损坏时必须重抓。
- 上次失败代码在重启时重试。
- 日期或 lookback 变化时开始新一轮状态，但旧逐代码缓存文件保留并按缓存有效性判断。

**进度**
- 每处理一只发出一个 `DailyProgressEvent`。
- `wall_time` 为带时区 ISO 字符串；`elapsed_seconds>=0`。
- `rate_per_minute = completed / elapsed_minutes`，零耗时时安全处理。
- `eta_seconds = pending / rate_per_second`；无速度时为 `None`，完成时为 0。
- `current_source` 为缓存命中时 `cache`，成功时实际来源，失败时空字符串。
- 文本错误不得包含候选、推荐、买入、卖出或概率解释。

**测试要求**
- RED：模块不存在。
- 首次成功写缓存与 checkpoint。
- 中断/失败后再次运行只重试失败代码，不重复成功代码。
- 断点称成功但缓存丢失时重新抓取。
- 同日有效缓存命中；损坏缓存重抓；跨日重抓。
- 上市不足 250 行按成功保存。
- 单只失败隔离、来源计数和 errors。
- 进度事件字段、速度、ETA、当前时间和中性措辞。
- 原子缓存与 checkpoint 写入失败不破坏旧文件。

**验证命令**
- `.\.venv\Scripts\python.exe -m pytest tests\test_daily_collector.py tests\test_daily.py -q`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\daily_collector.py marketbase\daily.py tests\test_daily_collector.py`

**全局约束**
- 不提交 Git，不修改范围外文件，不覆盖用户改动。
- 完成后写 `.superpowers/sdd/task-3-report.md`，包含 RED/GREEN、Ruff、自检、修改文件和顾虑。

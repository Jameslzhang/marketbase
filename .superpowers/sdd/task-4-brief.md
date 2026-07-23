### 任务 4：三市实时行情采集与独立客观审计

在 `D:\Environment\marketbase-main` 中实现与策略完全解耦的实时行情采集和数据质量审计。

**文件范围**
- 新建 `marketbase/market_collector.py`
- 新建 `marketbase/data_audit.py`
- 新建 `tests/test_market_collector.py`
- 新建 `tests/test_data_audit.py`
- 仅在复用/收紧客观采集链路确有必要时修改 `marketbase/live_workflow.py` 与 `tests/test_live_workflow.py`
- 不修改 `local_workflow.py`，不触碰候选、排名、推荐、交易计划模块。

**先阅读并复用**
- `marketbase/live_workflow.py` 中 `acquire_live_snapshot`、`fetch_reference_snapshot_with_bse_fallback`、`fetch_tencent_bse_snapshot`、`build_live_snapshot`、`validate_live_snapshot_freshness`
- `marketbase/snapshot.py` 的标准行情字段与数据源实现
- 现有 `tests/test_live_workflow.py`、`tests/test_snapshot.py`
- 保留已验证的新浪主行情、efinance 参考字段、em_datacenter + 腾讯北交所备用路径；不得退化北交所覆盖。

**公开接口**
- 冻结 dataclass `MarketCollectionResult`，至少包含：`frame: pd.DataFrame`、`audit: dict[str, object]`、`report: dict[str, object]`、`cache_path: Path`。
- `collect_market_snapshot(*, cache_path: str|Path, now: datetime|None=None, progress: Callable[[str],None]|None=None, primary_fetcher=None, reference_fetcher=None) -> MarketCollectionResult`
- `audit_market_snapshot(frame: pd.DataFrame, *, observed_at: datetime, expected_markets: tuple[str, ...] = ("sh", "sz", "bj"), provider_errors: Iterable[str] = ()) -> dict[str, object]`

测试注入参数可以在不破坏上述调用的前提下增加，但公开默认行为必须可直接调用真实链路。

**客观实时输出协议**
- 每行只输出固定字段：`code,name,market,price,volume,amount,turnover_rate,volume_ratio,quote_time,observed_at,source`。
- `code` 唯一六位数字；`market` 为 `sh/sz/bj`；按输入/采集顺序去重保留最后一条后再稳定排序为 `sh,sz,bj` + code，或明确测试并保持一种确定顺序。
- `price,volume,amount,turnover_rate,volume_ratio` 转为数值；不可用为 null/NaN，不填策略默认值。
- `observed_at` 为本轮带时区 ISO 时间；`quote_time` 保留供应商原始盘口时间（标准化为字符串）。
- `source` 为每行实际来源；如果当前合并链路只能提供分层来源，至少使用可审计的 `primary_source/reference_source` 元数据并给行设置实际报价来源。不得把缓存旧价格伪装成实时价格。
- 输出中禁止出现 `candidate/recommend/buy/sell/signal/score/rank/probability` 及对应中文策略字段。

**采集与备用源**
- 优先调用现有 `acquire_live_snapshot`；默认要求 `sh,sz,bj` 三市均有覆盖。
- 默认 reference 路径使用 `fetch_reference_snapshot_with_bse_fallback`，在 efinance 不可用或北交所不足时复用 `em_datacenter + fetch_tencent_bse_snapshot`。
- 北交所缺失不能因 `ValueError` 直接使整个本地进程没有任何可审计产物；采集器应记录 provider error/coverage gap，并在能取得部分客观快照时返回部分结果。只有所有主行情都无法取得或结果为空时才抛出清晰异常。
- `cache_path` 原子写 UTF-8 JSON；至少包含 `schema_version=1,generated_at,report,audit,rows`。旧缓存不得在失败时损坏。缓存只作恢复/参考，不得替代盘中实时价格并标成实时。
- `progress` 只打印带时间的采集阶段、行数、三市覆盖率、备用源和接口错误，不出现候选、推荐、买入、卖出等结论。

**客观审计协议**
- 返回至少包含：`observed_at,total_rows,unique_code_count,duplicate_code_count,invalid_code_count,market_counts,field_non_null_counts,field_coverage,latest_quote_time,stale_row_count,known_conditions,coverage_gaps,provider_errors`。
- `field_non_null_counts/field_coverage` 覆盖 `price,volume,amount,turnover_rate,volume_ratio,quote_time,source`。
- 市场缺失写 `missing_market_sh/sz/bj` 到 `coverage_gaps`；重复/非法代码、空价格、过期时间、供应商错误均客观记录。
- 已知条件与数据缺口分开：
  - `volume == 0` 且价格有效：`known_conditions` 记录停牌/无成交客观条件，不作为接口错误。
  - 量比为空但股票其余实时字段有效：记录 `volume_ratio_unavailable`；不得解释其好坏。若无法可靠识别新股，只记录字段不可用，不得根据名称或涨跌推断新股。
  - “上市不足 250 日”只能在存在明确日线行数元数据时记录；实时快照本身不能猜测。
- `provider_errors` 只保存来源、时间、错误文本等事实；错误文本需要中性化，禁止策略词。
- 审计不得导入筛选、prefilter、pipeline、afternoon 或任何策略模块。

**TDD 测试要求**
- RED：新模块不存在。
- 三市完整快照的固定列、类型、确定顺序、无策略字段。
- 重复代码、非法代码、缺失北交所、字段覆盖率。
- 零成交进入 known_conditions；量比缺失独立记录。
- 过期行情与 provider errors 进入 coverage_gaps/provider_errors。
- 主 reference 成功；efinance 失败后 em_datacenter + 腾讯北交所备用成功。
- 北交所备用也失败时仍返回可审计的沪深部分结果，不吞错误。
- 原子缓存写入失败不破坏旧文件；进度文本带时间且无策略措辞。
- 不得删除或削弱现有 `test_live_workflow.py` 的北交所和新鲜度测试。

**验证命令**
- `.\.venv\Scripts\python.exe -m pytest tests\test_market_collector.py tests\test_data_audit.py tests\test_live_workflow.py tests\test_snapshot.py -q`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\market_collector.py marketbase\data_audit.py marketbase\live_workflow.py tests\test_market_collector.py tests\test_data_audit.py`

**全局约束**
- 使用 TDD；一只/一个来源失败不得污染其他客观数据。
- 不提交 Git，不覆盖用户现有修改，不修改范围外文件。
- 完成后写 `.superpowers/sdd/task-4-report.md`，包含 RED/GREEN、变更文件、测试与 Ruff、残余风险。

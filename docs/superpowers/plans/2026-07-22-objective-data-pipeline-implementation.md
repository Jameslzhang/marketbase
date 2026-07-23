# MarketBase 客观数据管线实施计划

> **供执行代理使用：** 必须使用 `subagent-driven-development`（推荐）或 `executing-plans` Skill 按任务逐项实施。所有步骤使用复选框跟踪。

**目标：** 将本地 MarketBase 改造成只负责全市场客观数据采集、缓存、数学计算、审计和 Codex 定向补数的工具，彻底移除本地候选、排名和交易策略运行链路。

**架构：** 新增彼此独立的客观数据模块，复用已经验证的数据源与标准化函数。`local_workflow.py` 只保留全量采集和 `fulfill-request` 两个入口；全量采集刷新实时行情、250 日日线、中性指标和基础映射，Codex 请求入口只处理明确代码及当日分钟区间。

**技术栈：** Python 3.12、pandas、requests、pytest、Ruff；可选数据源依赖为 efinance、AkShare、Baostock、Tushare。

## 全局约束

- 本地不得建立候选池、排名、筛选、拒绝、推荐或交易结论。
- 日线历史固定保存最近 250 个交易日，上市历史不足时保留实际数据并记录原因。
- 分钟请求只允许当前交易日。
- 指标只输出数值和计算元数据，不输出方向标签或信号分。
- 全量日线刷新必须支持断点续跑、同日缓存命中、多源降级和原子写入。
- 控制台与日志只使用数据采集措辞，必须包含当前时间、累计耗时、覆盖率、速度、ETA 和接口错误。
- 不覆盖用户当前工作区中与本计划无关的改动。
- 当前 Git 未配置作者身份；每个任务完成后先精确暂存本任务文件，提交失败时保留暂存并记录，不擅自配置身份。

---

## 文件结构

### 新建

- `marketbase/neutral_indicators.py`：MA、RSI、MACD、ATR、VWAP 的纯数学计算。
- `marketbase/data_request.py`：Codex 请求校验、响应结构和原子 JSON 写入。
- `marketbase/daily_collector.py`：5528 只股票的 250 日日线全量缓存、断点恢复和进度事件。
- `marketbase/minute_collector.py`：当前交易日原始分钟线解析、区间裁剪和 VWAP。
- `marketbase/classification_map.py`：行业、概念、产业链基础映射合并及覆盖统计。
- `marketbase/data_audit.py`：实时、日线、指标、映射和请求响应的独立审计。
- `marketbase/market_collector.py`：全市场实时数据采集及标准化输出。
- `tests/test_neutral_indicators.py`
- `tests/test_data_request.py`
- `tests/test_daily_collector.py`
- `tests/test_minute_collector.py`
- `tests/test_classification_map.py`
- `tests/test_data_audit.py`
- `tests/test_market_collector.py`

### 重写或修改

- `local_workflow.py`：重写为全量采集与请求响应入口。
- `marketbase/live_workflow.py`：保留实时行情和北交所兜底，移除固定候选增强及解释性分钟指标。
- `marketbase/daily.py`：保留日线数据源能力，移除运行链路对解释性特征的依赖。
- `marketbase/chinese_output.py`：仅保留客观数据字段中文映射。
- `.vscode/launch.json`：改名为“一键客观数据采集”。
- `README.md`、`README.zh-CN.md`、`SKILL.md`、`pyproject.toml`：改为客观数据工具说明。

### 删除

- `marketbase/afternoon.py`
- `marketbase/full_market.py`
- `marketbase/prefilter.py`
- 本地入口引用的预筛、午后筛选、参考排名和策略输出测试。
- `strategies/` 与 `marketbase/strategies/` 中仅服务本地策略运行的 YAML。
- 经 `rg` 和导入测试确认不再被客观数据模块引用的评分、排名、风险、策略、报告与本地策略 API 模块。

---

### 任务 1：中性数学指标

**文件：**
- 新建：`marketbase/neutral_indicators.py`
- 新建：`tests/test_neutral_indicators.py`

**接口：**
- 输入：标准化日线 `DataFrame(date, open, high, low, close, volume, amount)`。
- 输出：`compute_daily_indicators(frame: pd.DataFrame) -> dict[str, object]`。
- 输入：分钟线 `DataFrame(time, price, volume, amount)`。
- 输出：`compute_vwap(frame: pd.DataFrame) -> float | None`。

- [ ] **步骤 1：编写失败测试，约束纯数值输出**

```python
def test_daily_indicators_return_only_values_and_metadata():
    frame = make_daily_frame(250)
    result = compute_daily_indicators(frame)
    assert set(result) == {
        "ma5", "ma10", "ma20", "ma60", "ma120", "ma250",
        "rsi14", "macd_dif", "macd_dea", "macd_hist",
        "atr14", "atr14_pct", "input_rows", "first_date",
        "last_date", "calculated_at",
    }
    assert not any(key in result for key in ("signal_score", "macd_status", "rsi_status"))
```

- [ ] **步骤 2：运行测试确认因模块不存在而失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q`

预期：导入 `marketbase.neutral_indicators` 失败。

- [ ] **步骤 3：实现 MA、RSI、MACD、ATR 和 VWAP**

实现要求：使用 pandas 指数移动平均和滚动窗口；输入不足时对应指标返回 `None`；不得返回任何字符串方向标签。

- [ ] **步骤 4：运行指标测试和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\neutral_indicators.py tests\test_neutral_indicators.py
```

预期：全部通过。

- [ ] **步骤 5：暂存并尝试提交**

```powershell
git add -- marketbase/neutral_indicators.py tests/test_neutral_indicators.py
git commit -m "feat: add neutral market indicators"
```

---

### 任务 2：Codex 数据请求与响应协议

**文件：**
- 新建：`marketbase/data_request.py`
- 新建：`tests/test_data_request.py`

**接口：**
- `load_data_request(path: str | Path, *, today: date) -> DataRequest`
- `write_data_response(path: str | Path, payload: dict[str, object]) -> Path`
- `DataRequest` 包含 `request_id`、唯一代码元组、日线配置和当日分钟区间。

- [ ] **步骤 1：编写合法请求、非法代码、历史分钟日期和未知字段测试**

```python
def test_request_rejects_non_current_minute_date(tmp_path):
    path = write_request(tmp_path, minute_date="2026-07-21")
    with pytest.raises(ValueError, match="当前交易日"):
        load_data_request(path, today=date(2026, 7, 22))

def test_request_deduplicates_nothing_and_rejects_duplicates(tmp_path):
    path = write_request(tmp_path, codes=["600519", "600519"])
    with pytest.raises(ValueError, match="重复股票代码"):
        load_data_request(path, today=date(2026, 7, 22))
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_data_request.py -q`

预期：模块导入失败。

- [ ] **步骤 3：实现冻结 dataclass、白名单校验和原子响应写入**

允许字段固定为：

```python
DAILY_FIELDS = frozenset({"raw", "ma", "rsi", "macd", "atr"})
MINUTE_FIELDS = frozenset({"raw", "vwap"})
```

临时文件必须与目标文件位于同一目录，写完后使用 `Path.replace()` 原子替换。

- [ ] **步骤 4：运行请求协议测试和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_data_request.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\data_request.py tests\test_data_request.py
```

- [ ] **步骤 5：暂存并尝试提交**

```powershell
git add -- marketbase/data_request.py tests/test_data_request.py
git commit -m "feat: add codex data request contract"
```

---

### 任务 3：全量日线缓存、限流和断点恢复

**文件：**
- 新建：`marketbase/daily_collector.py`
- 新建：`tests/test_daily_collector.py`
- 修改：`marketbase/daily.py`

**接口：**
- `collect_daily_universe(codes, *, cache_root, checkpoint_path, lookback=250, fetcher=fetch_daily_history, progress=None, now=None) -> DailyCollectionReport`
- `DailyCollectionReport` 记录总数、成功、缓存命中、失败、来源计数、开始时间、结束时间和错误。
- `progress` 接收 `DailyProgressEvent`，包含完成数、总数、速度、ETA、当前代码和来源。

- [ ] **步骤 1：编写断点恢复测试**

```python
def test_daily_collection_resumes_without_refetching_completed_codes(tmp_path):
    calls = []
    first = collect_daily_universe(
        ["600000", "000001"], cache_root=tmp_path / "cache",
        checkpoint_path=tmp_path / "checkpoint.json",
        fetcher=failing_after_first_fetcher(calls), now=fixed_now,
    )
    second = collect_daily_universe(
        ["600000", "000001"], cache_root=tmp_path / "cache",
        checkpoint_path=tmp_path / "checkpoint.json",
        fetcher=successful_fetcher(calls), now=fixed_now,
    )
    assert calls.count("600000") == 1
    assert second.success_count == 2
```

- [ ] **步骤 2：编写来源、时间和 ETA 进度测试**

断言每个进度事件包含 `wall_time`、`elapsed_seconds`、`rate_per_minute` 和 `eta_seconds`，日志文本不得包含“候选”“推荐”“买入”。

- [ ] **步骤 3：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_daily_collector.py -q`

- [ ] **步骤 4：实现逐代码原子缓存与断点文件**

缓存路径采用 `data/local_cache/daily_raw/<code>.json`，内容包含原始 250 日数据和来源元数据。断点文件每完成一只立即原子更新。有效同日缓存计为命中，不调用接口。

- [ ] **步骤 5：接入现有多源链路**

调用 `fetch_daily_history(code, lookback_days=250, source="auto", retries=2)`。保留现有 Tushare、腾讯、新浪、AkShare、Baostock 顺序及健康熔断；每次成功将实际来源写入缓存和报告。

- [ ] **步骤 6：运行测试、日线现有回归和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_daily_collector.py tests\test_daily.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\daily_collector.py marketbase\daily.py tests\test_daily_collector.py
```

- [ ] **步骤 7：暂存并尝试提交**

```powershell
git add -- marketbase/daily_collector.py marketbase/daily.py tests/test_daily_collector.py
git commit -m "feat: collect resumable full-market daily history"
```

---

### 任务 4：实时行情采集和独立审计

**文件：**
- 新建：`marketbase/market_collector.py`
- 新建：`marketbase/data_audit.py`
- 新建：`tests/test_market_collector.py`
- 新建：`tests/test_data_audit.py`
- 修改：`marketbase/live_workflow.py`

**接口：**
- `collect_market_snapshot(*, cache_path, now=None, progress=None) -> MarketCollectionResult`
- `audit_market_snapshot(frame, *, observed_at, expected_markets=("sh", "sz", "bj")) -> dict[str, object]`

- [ ] **步骤 1：编写三市覆盖、重复代码、时效和字段覆盖测试**

```python
def test_market_audit_reports_duplicate_and_missing_bse():
    audit = audit_market_snapshot(frame_with_duplicate_without_bse(), observed_at=NOW)
    assert audit["duplicate_code_count"] == 1
    assert audit["market_counts"]["bj"] == 0
    assert "missing_market_bj" in audit["coverage_gaps"]
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_market_collector.py tests\test_data_audit.py -q`

- [ ] **步骤 3：封装现有新浪、efinance、em_datacenter 和腾讯北交所链路**

只保留实时价格、成交量、成交额、换手率、量比及必要来源元数据。实时采集函数不得导入候选增强、行业评分或策略模块。

- [ ] **步骤 4：实现审计并分类客观缺失**

停牌导致的零成交、新股量比不可用和上市不足 250 日必须进入 `known_conditions`；接口错误、市场缺失、重复代码和过期行情进入 `coverage_gaps` 或 `provider_errors`。

- [ ] **步骤 5：运行新测试、现有实时测试和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_market_collector.py tests\test_data_audit.py tests\test_live_workflow.py tests\test_snapshot.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\market_collector.py marketbase\data_audit.py marketbase\live_workflow.py
```

- [ ] **步骤 6：暂存并尝试提交**

```powershell
git add -- marketbase/market_collector.py marketbase/data_audit.py marketbase/live_workflow.py tests/test_market_collector.py tests/test_data_audit.py tests/test_live_workflow.py
git commit -m "feat: add audited objective market collection"
```

---

### 任务 5：行业、概念和产业链基础映射

**文件：**
- 新建：`marketbase/classification_map.py`
- 新建：`tests/test_classification_map.py`

**接口：**
- `build_classification_map(snapshot, *, existing_map=None, supply_chain_path=None) -> tuple[pd.DataFrame, dict[str, object]]`

- [ ] **步骤 1：编写只合并基础事实、不推断标签的测试**

```python
def test_classification_map_preserves_source_labels_without_scoring():
    result, audit = build_classification_map(snapshot, existing_map=existing)
    assert result.loc[0, "industry"] == "半导体"
    assert result.loc[0, "concepts"] == "芯片,算力"
    assert "industry_score" not in result.columns
    assert "mainline" not in result.columns
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_classification_map.py -q`

- [ ] **步骤 3：实现来源优先级和覆盖审计**

实时快照非空值优先，其次使用已有缓存；产业链只读取明确维护的 `code,industry,concepts,supply_chain` 映射文件，不根据股票名称或涨跌推断。

- [ ] **步骤 4：运行测试和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_classification_map.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\classification_map.py tests\test_classification_map.py
```

- [ ] **步骤 5：暂存并尝试提交**

```powershell
git add -- marketbase/classification_map.py tests/test_classification_map.py
git commit -m "feat: add objective classification mappings"
```

---

### 任务 6：当前交易日分钟数据与 Codex 响应

**文件：**
- 新建：`marketbase/minute_collector.py`
- 新建：`tests/test_minute_collector.py`
- 修改：`marketbase/data_request.py`
- 修改：`marketbase/live_workflow.py`

**接口：**
- `collect_requested_data(request: DataRequest, *, daily_cache_root, minute_fetcher=fetch_tencent_minute_rows, now=None) -> dict[str, object]`
- `parse_minute_rows(rows) -> DataFrame(time, price, volume, amount)`
- `slice_minute_interval(frame, start, end) -> pd.DataFrame`

- [ ] **步骤 1：编写分钟区间裁剪、原始数据和 VWAP 测试**

```python
def test_requested_minute_response_contains_only_requested_interval():
    response = collect_requested_data(request_0931_0932, minute_fetcher=fake_rows)
    rows = response["data"]["600519"]["minute"]["raw"]
    assert [row["time"] for row in rows] == ["09:31", "09:32"]
    assert response["data"]["600519"]["minute"]["vwap"] == pytest.approx(10.15)
```

- [ ] **步骤 2：编写逐代码错误隔离测试**

一只代码接口失败时，其他代码数据仍写入响应；失败代码进入 `errors`，不得导致整个响应缺失。

- [ ] **步骤 3：运行测试确认失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_minute_collector.py tests\test_data_request.py -q`

- [ ] **步骤 4：实现机械补数和响应组装**

日线优先读取全量缓存；缓存缺失时只为请求代码调用现有日线多源接口并写回缓存。分钟线只调用当前交易日腾讯接口，按请求时间裁剪并保存原始行和中性 VWAP。

- [ ] **步骤 5：运行测试和 Ruff**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_minute_collector.py tests\test_data_request.py -q
.\.venv\Scripts\python.exe -m ruff check marketbase\minute_collector.py marketbase\data_request.py
```

- [ ] **步骤 6：暂存并尝试提交**

```powershell
git add -- marketbase/minute_collector.py marketbase/data_request.py marketbase/live_workflow.py tests/test_minute_collector.py tests/test_data_request.py
git commit -m "feat: fulfill objective codex data requests"
```

---

### 任务 7：重写一键入口和输出协议

**文件：**
- 重写：`local_workflow.py`
- 修改：`.vscode/launch.json`
- 重写：`tests/test_local_workflow.py`

**接口：**
- `run_collection(*, data_root, now=None, progress=print) -> dict[str, object]`
- `fulfill_request(*, request_path, response_path, data_root, now=None) -> dict[str, object]`
- `main()` 支持无参数全量采集及 `fulfill-request` 子命令。

- [ ] **步骤 1：重写入口测试，先明确禁止字段和禁止措辞**

```python
def test_default_collection_has_no_local_strategy_fields(tmp_path):
    summary = run_collection(data_root=tmp_path, providers=fake_providers)
    output = pd.read_csv(Path(summary["run_dir"]) / "market_snapshot.csv")
    forbidden = {"local_reference_score", "local_reference_rank", "signal_score"}
    assert forbidden.isdisjoint(output.columns)
    log = (Path(summary["run_dir"]) / "workflow.log").read_text(encoding="utf-8")
    assert not any(word in log for word in ("候选", "推荐", "买入", "卖出", "概率"))
```

- [ ] **步骤 2：编写输出文件和最新入口测试**

断言每次运行只生成 `market_snapshot.csv`、`market_snapshot.json`、`daily_indicators.csv`、`classification_map.csv`、`data_audit.json`、`manifest.json` 和 `workflow.log`，并原子更新最新交接 JSON。

- [ ] **步骤 3：运行测试确认旧入口失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -q`

- [ ] **步骤 4：重写入口并接入任务 1 至任务 6 的模块**

无参数入口不得调用 `head()`、`dynamic_candidate_limit`、预筛、午后扫描或候选增强。`fulfill-request` 读取固定请求路径，也允许通过 `--request` 和 `--response` 显式覆盖。

- [ ] **步骤 5：更新 VS Code 启动配置**

配置名称改为 `MarketBase: 一键客观数据采集`，仍启动根目录 `local_workflow.py`，不传策略参数。

- [ ] **步骤 6：运行入口测试和真实小样本冒烟测试**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -q
.\.venv\Scripts\python.exe -m ruff check local_workflow.py tests\test_local_workflow.py
```

- [ ] **步骤 7：暂存并尝试提交**

```powershell
git add -- local_workflow.py .vscode/launch.json tests/test_local_workflow.py
git commit -m "refactor: replace local strategy workflow with data collection"
```

---

### 任务 8：删除本地策略链路并更新文档和打包

**文件：**
- 删除：本地策略入口可达的预筛、午后筛选、评分、排名、推荐模块及其专用测试。
- 修改：`README.md`
- 修改：`README.zh-CN.md`
- 修改：`SKILL.md`
- 修改：`pyproject.toml`
- 修改：`marketbase/__init__.py`

**接口：**
- 打包后的命令只暴露客观数据采集和请求响应能力。

- [ ] **步骤 1：生成策略模块反向依赖清单**

运行：

```powershell
rg -n "afternoon|prefilter|local_reference_score|local_reference_rank|signal_score|strategy|ranker|scorer|recommend" local_workflow.py marketbase tests README.md README.zh-CN.md SKILL.md pyproject.toml
```

将仍被客观数据模块引用的底层通用函数迁移到对应新模块后，再删除原策略模块；不得通过保留无用导入让测试假通过。

- [ ] **步骤 2：删除策略 CLI、YAML、API 和专用测试**

删除后，`python -m marketbase.cli --help` 或替代入口不得显示策略、预筛、午后筛选、排名或推荐命令。

- [ ] **步骤 3：更新中英文文档和 Skill 说明**

文档只描述客观数据字段、250 日日线、当日分钟请求、数据审计、缓存、日志和 Codex 交接协议。

- [ ] **步骤 4：收紧依赖和包数据**

从 `pyproject.toml` 删除仅服务策略 YAML 或本地模型解释的依赖和包数据；保留 pandas、requests 及可选中国行情源依赖。

- [ ] **步骤 5：执行禁止字段扫描**

运行：

```powershell
rg -n "dynamic_candidate_limit|local_reference_score|local_reference_rank|signal_score|候选|推荐|买入|卖出|概率" local_workflow.py marketbase tests README.md README.zh-CN.md SKILL.md
```

预期：运行代码和当前文档中无本地策略字段或策略结论；测试中的禁止词断言可以保留。

- [ ] **步骤 6：运行全量测试、Ruff、打包和差异检查**

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m build
git diff --check
```

预期：测试、静态检查、构建和差异检查全部通过。

- [ ] **步骤 7：真实数据验收**

运行一次无参数入口，核对：

- 三市实时总行数和唯一代码数一致。
- 价格、成交量、成交额、换手率、量比覆盖率已记录。
- 日线任务遍历全市场并生成断点、速度和 ETA。
- 指标文件不存在策略字段。
- 合法请求生成 `codex_data_response.json`。
- 日志不存在策略结论措辞。

- [ ] **步骤 8：暂存并尝试提交**

```powershell
git add -A
git commit -m "refactor: make marketbase an objective data pipeline"
```

---

## 最终验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
.\.venv\Scripts\python.exe local_workflow.py fulfill-request --help
```

真实全量日线运行时间受接口限流影响，不以固定分钟数作为通过条件；通过条件是进度持续更新、ETA 可见、成功结果即时落盘、失败有来源链错误、程序中断后可以从断点继续。

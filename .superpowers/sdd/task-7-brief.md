### 任务 7：重写一键客观数据入口与输出协议

在 `D:\Environment\marketbase-main` 中彻底重写本地入口，使无参数启动只运行全市场客观数据采集，并提供 Codex 请求补数子命令。

**文件范围**
- 重写 `local_workflow.py`
- 重写 `tests/test_local_workflow.py`
- 修改 `.vscode/launch.json`
- 不修改任务 1-6 模块，除非发现阻断性接口缺陷并在报告中明确说明
- 不提交 Git。

**公开接口**
- `run_collection(*, data_root: str|Path, now: datetime|None=None, progress=print, providers: Mapping[str,object]|None=None) -> dict[str, object]`
- `fulfill_request(*, request_path: str|Path, response_path: str|Path, data_root: str|Path, now: datetime|None=None, providers: Mapping[str,object]|None=None) -> dict[str, object]`
- `main(argv: Sequence[str]|None=None) -> int`

`providers` 仅用于测试/可替换数据源，默认运行真实模块。允许键：`market_collector`（返回 `MarketCollectionResult`）、`daily_fetcher`、`existing_map`、`supply_chain_path`、`minute_fetcher`。不得通过 providers 注入策略。

**默认路径**
- 项目根运行时 `data_root = <repo>/data/daily_runs`。
- 固定请求/响应：`<data_root>/codex_data_request.json`、`<data_root>/codex_data_response.json`。
- 全量运行目录：`<data_root>/<YYYY-MM-DD>/<HHMMSS>_objective_data`（同秒冲突追加序号，不覆盖）。
- 持久缓存：`<data_root>/cache/market_snapshot.json`、`<data_root>/cache/daily/<code>.json`、`<data_root>/cache/daily_checkpoint.json`。
- 最新交接：`<data_root>/latest_codex_input.json`，原子更新，指向本次运行的绝对/可解析路径和缓存覆盖审计。

**run_collection 流程**
1. 建目录和只含客观措辞的 UTF-8 `workflow.log`，所有 progress 同时写终端与日志，消息带时区时间。
2. 调用 Task 4 `collect_market_snapshot`（或注入 collector），得到三市客观 frame/audit/report。
3. 使用 frame 的全部唯一代码调用 Task 3 `collect_daily_universe(...lookback=250...)`，不得 `head()`、不得固定 60、不得限制代码数量。
4. 对每个有效日线缓存调用 Task 1 `compute_daily_indicators`；失败代码保留在审计，不解释。`daily_indicators.csv` 每个成功代码一行，字段为 code + 中性指标固定字段。
5. 调用 Task 5 `build_classification_map`；`existing_map` 默认读取 `<data_root>/classification_map.csv`（若存在），显式产业链默认 `<data_root>/supply_chain_map.csv`（若存在）。本次结果原子更新持久 `classification_map.csv`。
6. 组装统一审计：实时 audit、日线 report/覆盖率/短历史/最新日期分布、指标成功失败、分类覆盖与 provider errors。
7. 只在 run_dir 生成恰好：`market_snapshot.csv,market_snapshot.json,daily_indicators.csv,classification_map.csv,data_audit.json,manifest.json,workflow.log`。原始日线不复制进运行目录。
8. 写 manifest（schema v1、generated_at、run_dir、files 文件名/行数/sha256、cache paths、错误摘要），再原子写 latest_codex_input.json。

**文件写入**
- JSON UTF-8、ensure_ascii=False、标准 JSON（禁止 NaN/Infinity）；CSV UTF-8-SIG 或 UTF-8，保持中文可读。
- 除 workflow.log 追加外，运行产物和 latest 文件使用临时文件 + replace，失败不留下半文件。
- `market_snapshot.json` 包含 schema/generated_at/rows；不把整个运行逻辑对象任意 dump。
- summary 至少返回 `run_dir,generated_at,market_rows,daily_success,daily_failure,indicator_rows,classification_rows,latest_input_path,files`。

**fulfill_request**
- `load_data_request(request_path, today=now.date())`，调用 Task 6 `collect_requested_data`，再用 `write_data_response` 原子写 response。
- 只使用 `<data_root>/cache/daily`，不触发全市场采集。
- 返回响应 payload；错误请求在任何 fetcher 调用前失败。

**CLI**
- 无参数：执行 `run_collection`，打印中文客观完成摘要，返回 0；失败打印数据采集错误并返回非 0。
- 子命令 `fulfill-request`，参数 `--request/--response/--data-root` 可覆盖默认值。
- 可选 `collect --data-root` 可与无参数等价，但不得有 scan/prefilter/afternoon/rank/recommend 等命令。
- VS Code 配置名称改为 `MarketBase: 一键客观数据采集`，程序仍为 `${workspaceFolder}/local_workflow.py`，不传策略参数。

**禁止项**
- 入口不得导入 `afternoon,prefilter,filter,pipeline,local_enrichment,full_market` 等策略/候选模块。
- 源码中不得出现 `dynamic_candidate_limit`、`.head(` 用于股票选择、`local_reference_score/local_reference_rank/signal_score`。
- 运行产物列、JSON 键和日志中不得出现中英文候选、推荐、买入、卖出、概率、排名、评分、主线、梯队等策略结论。
- 不生成候选 CSV、推荐文件、前60增强文件。

**TDD 测试**
- RED：旧入口默认流程/输出不符合新协议。
- 小型三市 fake market + 3 codes 验证全部代码传给 daily collector/fetcher，没有前60截断。
- run_dir 恰好 7 个文件，持久 cache/最新交接不混入 run_dir。
- 所有输出无禁用字段/措辞，日志只含采集进度、覆盖、错误。
- 日线缓存命中与失败审计；指标/分类行数；manifest 行数/hash/路径。
- latest_codex_input 原子更新且指向当前 run。
- fulfill_request 读取请求、只补明确代码、原子响应；非法请求不调用 provider。
- CLI 无参数与 fulfill-request 参数；`--help` 无策略命令。
- VS Code 中文名称与无策略参数。
- 测试必须完全离线，不得真实请求 5528 只。

**验证**
- `.\.venv\Scripts\python.exe -m pytest tests\test_local_workflow.py -q`
- `.\.venv\Scripts\python.exe -m ruff check local_workflow.py tests\test_local_workflow.py`
- 再运行任务 1-6 的定向测试，确认入口集成未破坏协议。

完成后写 `.superpowers\sdd\task-7-report.md`，含 RED/GREEN、输出样例结构、测试/Ruff、残余风险。

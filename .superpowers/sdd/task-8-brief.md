### 任务 8：删除本地策略链、收紧打包与最终验证

在 `D:\Environment\marketbase-main` 中完成最终架构清理。用户已明确：本地只负责客观数据，所有策略、候选、排名、推荐均由 Codex/云端负责。

**先做依赖审计**
- 使用 AST/`rg` 建立 `local_workflow.py` 与下列客观模块的真实 import 闭包：`classification_map,daily,daily_collector,data_audit,data_request,live_workflow,market_collector,minute_collector,neutral_indicators,snapshot,source_guard`。
- 任何被客观模块需要的通用函数保留/迁移后再删除旧模块；不得通过无用导入保住策略文件。
- 将清单和删除理由写入 `.superpowers/sdd/task-8-report.md`。

**目标保留模块**
- `marketbase/__init__.py`（重写，只导出客观 API）
- `marketbase/cli.py`（重写为客观入口薄封装，或打包脚本直接指向 `local_workflow:main`）
- `classification_map.py,daily.py,daily_collector.py,data_audit.py,data_request.py,live_workflow.py,market_collector.py,minute_collector.py,neutral_indicators.py,snapshot.py,source_guard.py`
- 仅当依赖审计证明需要时保留其他底层模块，并在报告说明。

**必须删除/移除可达性**
- 删除本地策略模块：`afternoon,audit,candidate_context,chinese_output,config,context,doctor,dsa,dsa_adapter,dsa_provider,evaluate,filter,full_market,hotspot,industry,local_enrichment,local_snapshot,normalize,overview,performance_history,pipeline,post_analysis,prefilter,ranker,report,result_schema,risk,run_history,scorer,server,snapshot_us,source_history,store,strategy,strategy_cards,strategy_templates`，除非依赖审计证明某个客观底层仍需保留；若需保留必须去策略化改名/迁移。
- 删除 `marketbase/strategies/` YAML 与策略说明。
- 删除上述模块的专用测试；保留并修复客观测试：`test_classification_map,test_daily,test_daily_collector,test_data_audit,test_data_request,test_live_workflow,test_local_workflow,test_market_collector,test_minute_collector,test_neutral_indicators,test_snapshot`，以及确有必要的 source_guard 测试。
- `daily.py` 当前混有 `signal_score` 等策略计算：重写为纯日线多源获取/标准化/来源错误功能，保持 `fetch_daily_history(code,lookback_days,source,retries)` 对任务 3/6 兼容；删除 enrich_daily_features、compute_daily_features、信号分等。
- `live_workflow.py` 删除 `compute_minute_confirmation` 的 above/hold/activity 解释和候选 profile/enrich 函数；只保留三市实时采集、北交所 fallback、快照构建/新鲜度、腾讯原始分钟行。

**CLI/打包**
- `marketbase --help` 与 `python -m marketbase.cli --help` 只显示 `collect`、`fulfill-request`（及全局 data-root），不得显示 screen/prefilter/afternoon/rank/strategy/serve/evaluate。
- 保证 wheel 安装后入口可运行。可将 orchestration 移入包内客观模块并让根 `local_workflow.py` 薄封装，或在 setuptools 中正确包含 `local_workflow` py-module；选择最小可靠方式并写安装冒烟测试。
- `pyproject.toml` 描述改为客观 A 股数据采集管线；删除 `pyyaml`、LLM、US 行情等仅策略依赖；保留 pandas、requests，CN 数据源放可选依赖。`full` 不含 litellm/yfinance。
- 删除策略 YAML package data；保留必要 SKILL/LICENSE 数据。
- `marketbase.__all__` 只导出客观采集、请求、审计、指标 API。

**文档**
- 从头精简重写 `README.md`、`README.zh-CN.md`、`SKILL.md`，只描述：
  - 本地/云端职责边界
  - 一键全市场实时 + 250 日日线 + 中性指标 + 基础映射 + 审计
  - 缓存、断点、速度/ETA/错误日志
  - `codex_data_request.json` 与 `codex_data_response.json`
  - 当日分钟限制和 raw/VWAP
  - CLI/VS Code 使用方式、输出 7 文件、latest 交接
  - 数据来源/限流/免责声明（不构成投资建议）
- 文档不得描述本地候选、打分、排名、选股策略、买卖区间或概率。
- 删除/归档当前项目内策略专用 docs/examples（如 `docs/afternoon-screening-strategy.md`、旧策略示例）；保留本次架构设计/实施文档可作为开发记录，但正式 README 不链接策略文档。

**禁止扫描**
- 运行代码（客观模块+入口）不得含：`dynamic_candidate_limit,local_reference_score,local_reference_rank,signal_score,prefilter,afternoon,candidate,recommend,buy,sell,probability,mainline,tier` 或对应中文策略结论。允许：
  - 中性化禁词常量/测试断言中出现这些字面量；
  - `source_order/rank` 若仅指数据源优先顺序，应改名避免误判更佳。
- 输出字段不得含策略词。

**TDD/验证**
1. 先更新/新增 CLI 与包导出测试，确认旧入口失败。
2. 完成删除与重写。
3. 运行：
   - `.\.venv\Scripts\python.exe -m pytest -q`
   - `.\.venv\Scripts\python.exe -m ruff check .`
   - `.\.venv\Scripts\python.exe -m build`
   - `git diff --check`
   - `.\.venv\Scripts\python.exe -m marketbase.cli --help`
   - `.\.venv\Scripts\python.exe local_workflow.py fulfill-request --help`
4. 构建后在临时 venv 安装 wheel，执行 `marketbase --help` 冒烟，证明入口不是只在源码树可用。
5. 运行任务 1-7 全部客观测试。
6. 做离线小样本端到端 run_collection + fulfill_request，核对 7 文件、latest 与标准 JSON。

**真实数据验收边界**
- 不在本任务中启动可能耗时数小时的 5528×250 日全量网络抓取，以免不可控限流；完成代码后可运行真实三市实时采集小冒烟并记录结果。全量日线的验收条件由断点、持续进度、ETA、逐代码缓存和失败隔离测试证明。
- 若真实接口当时不可用，记录 provider errors，不伪造成功。

**全局约束**
- 使用 TDD；不提交 Git；不配置 Git 身份；不覆盖范围外用户文件。
- 删除是用户明确要求，但先验证客观 import 闭包。
- 完成后追加 `.superpowers/sdd/task-8-report.md`，列出保留/删除文件、测试、构建、安装冒烟、真实接口结果与残余风险。

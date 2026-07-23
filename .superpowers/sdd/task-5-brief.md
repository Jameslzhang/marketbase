### 任务 5：行业、概念和产业链基础映射

在 `D:\Environment\marketbase-main` 中实现纯事实分类映射，不进行评分、推断或推荐。

**文件范围**
- 新建 `marketbase/classification_map.py`
- 新建 `tests/test_classification_map.py`
- 不修改其他文件，不提交 Git。

**公开接口**
- `build_classification_map(snapshot: pd.DataFrame, *, existing_map: pd.DataFrame|None=None, supply_chain_path: str|Path|None=None) -> tuple[pd.DataFrame, dict[str, object]]`

**输出协议**
- 输出固定字段：`code,name,industry,concepts,supply_chain,industry_source,concepts_source,supply_chain_source,updated_at`。
- `code` 必须为唯一六位数字，按 snapshot 的首次合法代码顺序输出；非法/重复事实进入 audit。
- snapshot 中非空 `name/industry/concepts` 优先；空值才由 existing_map 补充。
- `supply_chain_path` 只读取明确维护的 UTF-8 CSV，允许列 `code,industry,concepts,supply_chain`；其中非空字段仅补 snapshot/existing_map 仍为空的值，`supply_chain` 无其他来源时直接使用。
- 不得根据股票名称、价格、涨跌、行业或概念推断产业链；不得自动添加半导体、医疗、石油等标签。
- concepts 与 supply_chain 保留来源文本，最多做去首尾空白和明确的分隔符规范化；不得做语义扩展、排名或解释。
- 来源列必须准确标明 `snapshot/existing_map/supply_chain_file/empty`。
- `updated_at` 使用来源中已有客观更新时间的最近有效值；若均无则为空，不使用运行时刻伪装数据更新时间。

**审计协议**
- audit 至少包含：`total_snapshot_rows,output_rows,unique_code_count,duplicate_code_count,invalid_code_count,industry_coverage_count,concepts_coverage_count,supply_chain_coverage_count,missing_industry_codes,missing_concepts_codes,missing_supply_chain_codes,source_counts,errors`。
- supply_chain 文件不存在、无法读取、缺列、非法/重复代码作为中性 `errors` 记录；文件读取失败不得丢失 snapshot/existing_map 的结果。
- 禁止出现 `score,rank,signal,candidate,recommend,buy,sell,probability,mainline,tier` 及对应策略字段/文本。

**TDD 测试**
- RED：模块不存在。
- snapshot 非空字段优先，existing_map 只补空值。
- 显式 CSV 补产业链，且不会根据名称/涨跌推断。
- 来源列准确；空值来源为 empty。
- 重复/非法代码审计，输出仍唯一合法且顺序稳定。
- 文件缺失/缺列/非法记录错误隔离。
- 输出无任何策略字段或策略文本。
- 输入对象不被原地修改。

**验证**
- `.\.venv\Scripts\python.exe -m pytest tests\test_classification_map.py -q`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\classification_map.py tests\test_classification_map.py`

完成后写 `.superpowers\sdd\task-5-report.md`，包含 RED/GREEN、文件、测试、Ruff 与残余风险。

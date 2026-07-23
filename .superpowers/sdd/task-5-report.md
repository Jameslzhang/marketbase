# 任务 5 完成报告

## RED/GREEN

- RED：先新增 `tests/test_classification_map.py`，运行定向 pytest，因 `marketbase.classification_map` 尚不存在而按预期出现 `ModuleNotFoundError`。
- GREEN：新增最小事实映射实现后，定向测试通过；补充 CSV 非法/重复记录隔离覆盖后仍保持通过。

## 文件

- `marketbase/classification_map.py`
- `tests/test_classification_map.py`
- `.superpowers/sdd/task-5-report.md`

实现固定输出字段，严格保留快照首次合法六位代码顺序；按 `snapshot -> existing_map -> supply_chain_file -> empty` 标注字段来源；只读取显式 UTF-8 CSV；不根据名称、价格、涨跌、行业或概念推断产业链，也不输出评分、排序或推荐字段/文本。

## 验证

- `.\.venv\Scripts\python.exe -m pytest tests\test_classification_map.py -q`
  - `7 passed`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\classification_map.py tests\test_classification_map.py`
  - `All checks passed!`
- 未提交 Git。

## 残余风险

- 产业链文件必须是 UTF-8 CSV，且包含 `code,industry,concepts,supply_chain` 列；缺失、读取失败、非法或重复记录只进入审计错误。
- 更新时间只采用来源中可解析的 `updated_at` 客观值；没有有效值时保持空字符串，不生成运行时更新时间。

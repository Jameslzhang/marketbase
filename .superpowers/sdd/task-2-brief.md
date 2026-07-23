### 任务 2：Codex 数据请求与响应协议

在 `D:\Environment\marketbase-main` 中实现请求校验与原子响应写入。

**文件范围**
- 新建 `marketbase/data_request.py`
- 新建 `tests/test_data_request.py`

**公开接口**
- `load_data_request(path: str | Path, *, today: date) -> DataRequest`
- `write_data_response(path: str | Path, payload: dict[str, object]) -> Path`
- 冻结 dataclass：`DataRequest`, `DailyRequest`, `MinuteRequest`

**请求格式**
- 顶层必需：`schema_version=1`, 非空 `request_id`, 非空 `codes`。
- `codes`：唯一六位数字 A 股代码；保持输入顺序；重复、空值或非六位数字直接报错。
- `daily` 可选；存在时 `lookback` 只能为 1..250，`fields` 非空且只能来自 `raw,ma,rsi,macd,atr`。
- `minute` 可选；存在时 `date` 必须等于 `today.isoformat()`，`start/end` 使用 `HH:MM`，开始不得晚于结束，字段只能来自 `raw,vwap`。
- `daily` 与 `minute` 至少存在一个。
- 拒绝未知顶层字段及子对象未知字段，避免拼写错误静默通过。
- 错误使用中文、可执行提示，并在任何数据接口调用前抛出 `ValueError`。

**响应写入**
- `write_data_response` 以 UTF-8、`ensure_ascii=False`、缩进 2 写 JSON。
- 目标父目录自动创建。
- 使用同目录唯一临时文件，写完后 `Path.replace()` 原子替换。
- 写入失败时清理临时文件，不破坏已有响应。
- 返回解析后的绝对 `Path`。

**测试**
- 合法完整请求和仅日线/仅分钟请求。
- 重复代码、非法代码、错误 schema、空 request_id。
- 历史分钟日期、错误时间格式、start > end。
- 未知字段、非法 lookback、非法字段白名单。
- 原子响应覆盖、中文不转义、写入失败保留旧文件。
- 先 RED 再 GREEN。

**验证命令**
- `.\.venv\Scripts\python.exe -m pytest tests\test_data_request.py -q`
- `.\.venv\Scripts\python.exe -m ruff check marketbase\data_request.py tests\test_data_request.py`

**全局约束**
- 不修改任务范围外文件，不提交 Git。
- 不包含排名、候选、推荐或交易字段。
- 完成后写 `.superpowers/sdd/task-2-report.md`，记录 RED/GREEN 命令、输出、自检和顾虑。

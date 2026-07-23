### 任务 1：中性数学指标

在 `D:\Environment\marketbase-main` 中实现纯数学指标模块。

**文件范围**
- 新建 `marketbase/neutral_indicators.py`
- 新建 `tests/test_neutral_indicators.py`

**接口**
- `compute_daily_indicators(frame: pd.DataFrame, *, calculated_at: datetime | None = None) -> dict[str, object]`
- `compute_vwap(frame: pd.DataFrame) -> float | None`

**输入**
- 日线列：`date, open, high, low, close, volume, amount`
- 分钟列：`time, price, volume, amount`

**输出键必须严格为**
- `ma5`, `ma10`, `ma20`, `ma60`, `ma120`, `ma250`
- `rsi14`
- `macd_dif`, `macd_dea`, `macd_hist`
- `atr14`, `atr14_pct`
- `input_rows`, `first_date`, `last_date`, `calculated_at`

**行为**
- MA 使用收盘价滚动均值。
- RSI14 使用 Wilder 平滑方法。
- MACD 使用 EMA12、EMA26、DEA9，柱值为 `(DIF - DEA) * 2`。
- ATR14 使用 True Range 的 Wilder 平滑；`atr14_pct = atr14 / 最新收盘价 * 100`。
- VWAP 使用 `sum(amount) / sum(volume)`；如果分钟成交量单位为“手”，调用方应先标准化为股，本函数不猜测单位。
- 输入不足时对应指标返回 `None`，不得抛出无意义异常。
- 日期输出 ISO 文本；`calculated_at` 使用传入时间，未传入时使用本地时区当前时间。
- 输出不得包含 `signal_score`、`macd_status`、`rsi_status`、多空或好坏文本。
- 数值保留合理精度，不要求为展示做格式化。

**TDD 与验证**
1. 先写测试并运行，确认因模块不存在而失败。
2. 实现最小代码使测试通过。
3. 覆盖 250 行完整输入、短历史、零成交量 VWAP、已知 VWAP 和禁止字段。
4. 运行：
   - `.\.venv\Scripts\python.exe -m pytest tests\test_neutral_indicators.py -q`
   - `.\.venv\Scripts\python.exe -m ruff check marketbase\neutral_indicators.py tests\test_neutral_indicators.py`

**全局约束**
- 不修改任务范围外文件。
- 不覆盖现有用户改动。
- 当前 Git 未配置作者身份，不执行提交。
- 完成后将详细报告写入 `.superpowers/sdd/task-1-report.md`，包含修改文件、RED/Green 测试命令与输出、自检和顾虑。

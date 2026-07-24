# 自选池隔夜短线策略消费者规范

状态：研究规范  
文档版本：1.0  
适用市场：沪深 A 股  
时区：Asia/Shanghai  

## 1. 文档目的

本文定义一个可配置、可复现、可审计的“封闭自选池 + 全市场环境验证”
隔夜短线策略。策略在独立的决策层运行，MarketBase 只提供客观数据。

本文不包含：

- 固定用户自选股；
- 单日盘面结论；
- 本机绝对路径；
- 人工指定的个股买卖价；
- 未校准的数值胜率；
- 自动交易或券商接入。

同一份合格输入、相同的策略与参数版本，应得到相同输出。

## 2. 与 MarketBase 的边界

MarketBase 负责：

- 市场快照；
- 日线历史；
- 中性 MA、RSI、MACD、ATR 等指标；
- 基础分类映射；
- 当日分钟线和 VWAP 请求；
- 数据来源、时间戳、质量和审计记录。

策略消费者负责：

- 管理自选池版本；
- 计算市场环境和全市场相对强弱；
- 识别交易通道；
- 生成买区、禁追价、保护位和目标区；
- 保存不可变决策快照；
- 维护影子样本和概率模型；
- 输出交易计划和次日退出协议。

策略字段不得回写或伪装成 MarketBase 的客观数据字段。MarketBase 数据不构成
投资建议；策略消费者也不得把数据完整性错误解释为交易信号。

## 3. 策略目标

策略只持有一个夜晚：

```text
午后确认
→ 当日尾盘入场
→ 下一交易日退出
→ 最迟 14:30 提交全部剩余退出意图
```

约束：

- A 股 T+1；
- 不跨第二个夜晚；
- 不把失败的短线仓位改称波段持有；
- 每日最多一个正式候选；
- 没有合格买点时明确空仓。

## 4. 自选池合同

自选池由策略调用方配置，不在本文硬编码。建议结构：

```json
{
  "schema_version": "WATCHLIST_V1",
  "watchlist_version": "example-v1",
  "effective_from": "YYYY-MM-DD",
  "effective_to": null,
  "changed_at": "ISO-8601",
  "changed_by": "user_or_system_id",
  "change_reason": "manual_review",
  "symbols": [
    {"code": "000001", "name": "示例证券"}
  ]
}
```

规则：

- 正式候选只能来自当前有效自选池；
- 全市场股票只用于环境、行业、RPS、流动性和可执行性验证；
- 交易日 09:25 后的名单变更从下一交易日生效；
- 历史分析必须绑定当时有效的 `watchlist_version`；
- 池外股票不得替换或挤占正式候选名额。

## 5. 时间截面与防泄漏

默认检查点：

```text
13:00
14:05
14:25
14:45
```

时间规则：

- 只使用在决策时点前已经完成且已经可用的数据；
- 每个字段保存 `as_of` 和 `available_at`；
- 分钟判断只能使用已完成分钟；
- 新信号必须在 14:35 前完成第三根确认分钟；
- 14:35 后只能维持、降级或取消已有信号；
- 14:45 发布最终计划；
- 默认入场窗口为 14:45 至 14:55；
- 每次显示“仍可执行”前，用不超过 90 秒的新数据复核；
- 盘后重建只能用于诊断，不得改变生产决策或概率训练标签。

每个检查点必须独立保存。后续检查点不得覆盖前序证据。

## 6. 数据合同

### 6.1 必需数据

策略至少需要：

- 自选池证券实时快照；
- 自选池证券当日已完成分钟线；
- 足够长度、复权口径明确的日线；
- MA6、MA11、MA23、RSI14、MACD、ATR14；
- 沪深主要宽基及成长指数快照和当日分钟线；
- 沪深 A 股合格 RPS 宇宙；
- 行业映射、行业成员和行业实时快照；
- 合规事件证据；
- 证券状态、涨跌停价和最小价格变动单位；
- 费用、滑点和账户风险配置。

日线记录必须声明：

```text
adjustment_method
adjustment_source
corporate_action_flag
```

如果数据源只能提供不复权日线，且回看窗口内存在除权除息、拆并股或其他价格跳变，
依赖 MA、ATR、收益率和结构价格的规则必须返回 `data_insufficient`。

### 6.2 MarketBase 交接

消费者应首先检查：

```text
latest_codex_input.json
manifest.json
data_audit.json
market_snapshot.json
daily_indicators.csv
classification_map.csv
```

需要当日分钟线或补充日线时，通过 `codex_data_request.json` 请求。MarketBase
不提供历史分钟线，因此生产消费者必须在交易日内及时保存所需分钟证据。

### 6.3 数据不足

以下情况返回 `data_insufficient`，不得猜测：

- 缺少完整自选池；
- 报价过期或来源冲突；
- 分钟线不足；
- 指标历史不足；
- 全市场 RPS 覆盖不足；
- 行业成员或行业快照不足；
- 事件缺少官方来源或有效时间；
- 成交量、成交额单位无法确认；
- 费用或证券交易规则缺失。

### 6.4 RPS 宇宙

RPS 宇宙必须在交易日 09:25 前冻结，并保存成分哈希。沪深 A 股默认排除：

- ST、退市整理或风险警示状态不明；
- 停牌、零成交或报价异常；
- 上市时间不足目标回看窗口；
- 北交所证券；
- 当日参考价异常且无法完成复权校验的证券。

RPS20 至少要求 21 个口径一致的完成交易日收盘价，以首尾价格形成 20 日收益区间。
合格宇宙覆盖率不足 95% 时不得用于生产。
池内排名不能替代全市场 RPS。

所有宇宙过滤规则必须绑定 `rps_universe_version`。

## 7. 单位标准

标准字段：

```text
volume_raw
volume_raw_unit
volume_semantics
volume_shares
amount_raw
amount_raw_unit
amount_semantics
amount_cny
unit_conversion_version
```

所有 VWAP 只能使用：

```text
vwap = amount_cny / volume_shares
```

如果供应商提供累计量额，必须先计算相邻分钟差值。单位未知、累计值负差或跨日未
重置时，相关 VWAP 和活动度无效。

## 8. 市场环境

市场环境在开盘前和每个决策时点冻结。建议标签：

```text
trend_expansion
rotation_range
risk_contraction
regime_data_insufficient
```

至少使用：

- 沪深 300；
- 中证 1000；
- 创业板指；
- 科创 50；
- 全市场上涨家数比例；
- 成交额相对历史水平；
- 指数相对日内 VWAP；
- 尾盘方向和波动。

环境公式和阈值必须版本化。盘后不得根据结果重新标记当日环境。

正式实现必须提供 `market_regime_policy_id` 和 `market_veto_policy_id`。缺少冻结政策
时，市场环境只能用于展示，不能允许正式候选通过。

## 9. 交易通道

每只股票可同时被多个通道评估，但必须按固定优先级冻结唯一主通道，禁止盘后选择
表现最好的标签。

本节所有日线背景只使用决策日前一个完成交易日及更早数据。

### 9.1 趋势延续

标识：

```text
trend_continuation
```

默认背景：

```text
close > MA11
MA11 >= MA23
RPS20 >= 70
```

还应满足：

- MACD 动能改善；
- RSI 和布林位置未触发过热；
- 最近三个完成交易日没有重复长上影分布风险。

### 9.2 强势回踩收复

标识：

```text
strong_pullback_reclaim
```

默认背景：

```text
close > MA23
return_20d >= 0.08
```

盘中必须出现可验证回踩，并重新收复全天 VWAP、锚定 VWAP 或回踩枢轴。

### 9.3 行业反转挑战

标识：

```text
sector_reversal_challenger
```

适用于个股背景偏弱、但行业出现第一日反转且个股为成交核心的情况。

该通道必须定义并冻结：

- “第一日”的前序状态；
- 行业最少有效成员数量；
- 行业上涨广度；
- 行业平均涨幅；
- 行业排名和并列规则；
- 个股成交核心排名；
- 行业映射生效时间。

新通道在达到影子审计门槛前必须保持 `production_buyable=false`。

## 10. 行业与事件证据

### 10.1 行业同期证据

行业映射必须在交易日 09:25 前冻结。建议最低质量门槛：

```text
valid_member_count >= 20
valid_member_coverage >= 0.80
```

行业强度可以由以下任一条件确认：

```text
industry_rank <= 5
```

或：

```text
advance_breadth >= 0.70
average_return >= 0.02
```

“第一日反转”的默认定义为：前一完成交易日不满足行业强度条件，而当前决策时点
首次满足。实现若使用更长冷却窗口，必须通过版本化政策声明。

个股还必须是行业成交核心。默认成交核心为行业成交额前两名，或位于行业前 10%
且当日成交额不少于 10 亿元。成交核心算法、排名范围和并列规则必须版本化，不能
由语言模型临时判断。

### 10.2 合规事件证据

事件至少保存：

```text
event_id
event_type
official_source
published_at
available_at
valid_from
valid_to
quantitative_fields
status
```

只有在决策时已经公开、仍在有效期内且具有可验证量化字段的官方事件才能通过。
媒体报道、互动平台表述、框架协议和无法量化的叙事不能成为合规事件证据。合规
事件可以满足“行业或事件”证据分支，但不能绕过主通道、买区和风险条件。

## 11. 结构价格

分钟摆动点使用左右各两根已完成分钟：

```text
minute_swing_high(i) =
    high[i] > max(high[i-2], high[i-1])
    and high[i] >= max(high[i+1], high[i+2])

minute_swing_low(i) =
    low[i] < min(low[i-2], low[i-1])
    and low[i] <= min(low[i+1], low[i+2])
```

摆动点在 `i+2` 分钟完成后才可用。左侧严格、右侧非严格，使平台价格只识别
最早出现的一根。

通道枢轴：

- 趋势延续：`named_pivot`；
- 强势回踩：`reclaim_pivot`；
- 行业反转：`reversal_pivot`。

所有枢轴必须在确认窗口开始前可用，不能使用确认后的价格反推。

## 12. 分钟确认

默认确认合同：

```text
confirmation_minutes = 3
activity_ratio_min = 0.90
confirmation_cutoff = 14:35
```

通过条件：

1. 连续三根已完成分钟收盘均不低于 `trigger_reference`；
2. 第三根确认分钟不晚于 14:35；
3. 三根分钟成交量和成交额均大于零；
4. 第三根收盘不低于第一根收盘；
5. 三分钟成交额之和不低于基准窗口中位数的 90%。

基准窗口为确认前 15 个完成分钟内所有连续三分钟成交额之和的中位数。

## 13. 触发基准

```text
trend_continuation:
    trigger_reference =
        max(full_day_vwap, afternoon_avwap, named_pivot)

strong_pullback_reclaim:
    trigger_reference =
        max(reclaimed_vwap, reclaim_pivot)

sector_reversal_challenger:
    trigger_reference =
        max(reversal_avwap, reversal_pivot)
```

任一必需输入缺失时，该通道为 `data_insufficient`。

## 14. 买区与禁追价

默认禁追价：

```text
no_chase_price = min(
    trigger_reference * 1.015,
    confirmation_close + 0.15 * ATR14
)
```

默认买区：

```text
buy_zone_lower = max(
    trigger_reference,
    confirmation_close - 0.10 * ATR14
)

buy_zone_upper = min(
    no_chase_price,
    confirmation_close
        + min(0.15 * ATR14,
              confirmation_close * 0.005)
)
```

取整规则：

- 禁追价向下取至最小价格单位；
- 买区下沿向上取；
- 买区上沿向下取。

最新正常可成交价格必须位于闭区间：

```text
[buy_zone_lower, buy_zone_upper]
```

买区发布后不能跟随价格移动。价格超过上沿、超过禁追价、跌破触发基准、证据
失效或数据过期时，入场状态转为 `canceled`。

## 15. 可执行性

正常报价不等于正常可成交。消费者必须检查：

- 停牌；
- 一字涨停或一字跌停；
- 临时停牌；
- 单分钟瞬时开板；
- 买卖方向可用成交量；
- 计划数量与可成交量；
- 价格限制和最小价格单位；
- 数据来源时间。

交易指令只能记录“计划”或“退出意图”，不能宣称保证成交。

## 16. 费用与仓位

费用配置至少包含：

- 买入佣金率；
- 卖出佣金率；
- 每笔最低佣金；
- 卖出印花税；
- 双边过户费；
- 双边滑点；
- 账户特定覆盖值。

由于最低佣金依赖最终数量，仓位不能用一次除法计算。应按 100 股逐档枚举，逐档
重新计算费用、计划损失和压力损失，再选择满足全部限制的最大数量。

未提供新鲜账户快照时，只输出 `plan_only`，不输出账户感知股数。

风险上限必须由独立的 `risk_policy_id` 冻结，至少约束：

- 单笔资金占用；
- 计划保护位损失；
- 下一交易日跌停压力；
- 多日无法退出压力；
- 隔夜总压力；
- 同行业相关性压力。

## 17. 保护位

当前通道枢轴：

```text
current_channel_pivot =
    named_pivot       if trend_continuation
    reclaim_pivot     if strong_pullback_reclaim
    reversal_pivot    if sector_reversal_challenger
```

结构支撑是在买区下沿以下、确认前已经可用的最高有效价格，来源只能是：

- `trigger_reference`；
- `current_channel_pivot`；
- `afternoon_swing_low`。

保护位：

```text
raw_protection = max(
    structural_support - 0.15 * ATR14,
    buy_zone_upper - 0.80 * ATR14
)

protection_price = min(
    raw_protection,
    buy_zone_lower - 2 * tick_size
)
```

保护位向下取整。它是下一交易日的退出触发参考，不是最大损失保证。

## 18. 目标区与交易经济性

阻力位只能使用决策时已经确认、且严格高于买区上沿的日线摆动高点和当日高点。
ATR14 使用前一完成交易日、复权口径一致的日线计算。

```text
first_center = min(
    valid_resistance_1,
    buy_zone_upper + 0.75 * ATR14
)

second_center = min(
    valid_resistance_2,
    buy_zone_upper + 1.25 * ATR14
)
```

没有有效阻力时，只使用 ATR 项。

```text
first_zone =
    [first_center - 0.05 * ATR14,
     first_center + 0.05 * ATR14]

second_zone =
    [second_center - 0.05 * ATR14,
     second_center + 0.05 * ATR14]
```

正式候选必须满足：

```text
first_zone_net_reward > 0
reward_risk_ratio >= 1.20
```

收益和损失必须计入费用与滑点，并使用买区上沿进行保守计算。100 股交易也必须
独立满足经济性要求。

## 19. 正式候选

正式候选必须同时通过：

- 有效主通道；
- 数据完整；
- 三分钟确认；
- VWAP 或锚定 VWAP；
- 活动度；
- 行业或合规事件证据；
- 买区和禁追价；
- 正常可成交；
- 交易经济性；
- 保护位可构造；
- 账户风险或 `plan_only` 边界；
- 市场否决；
- 时间窗口。

每日正式候选最多一只。没有合格候选时：

```text
formal_candidate = null
position = empty
```

“自选池中大概率会有股票上涨”不能替代绝对执行门槛。

## 20. 入场状态机

建议状态：

```text
unassessed
→ data_insufficient / rejected / deep_watch
→ conditional_watch
→ confirmed_candidate
→ active / canceled / expired
→ executed / execution_unknown
```

规则：

- `canceled` 和 `expired` 是当日终态；
- 14:55 后不得恢复买入资格；
- 已发布候选不能静默替换；
- 用户实际成交必须单独记录，不能从行情推定。

## 21. 次日退出协议

### 21.1 一个交易单位

只有一个可卖单位时：

- 第一目标区首次正常可执行触发后全部退出；
- 不等待第二目标区；
- 保护位先发生时提交全部退出意图；
- 14:30 提交全部剩余退出意图。

### 21.2 可拆分仓位

至少两个可拆分单位时：

- 第一目标区默认退出 50%；
- 只有 `extension_allowed=true` 才保留剩余仓位；
- 剩余仓位进入第二目标、保护退出或 14:30 时间退出路径。

### 21.3 不可退出

跌停、停牌或流动性不足时标记：

```text
exit_unavailable
```

不得把保护位描述为保证成交价。

## 22. 预测与排名层

预测层和执行层必须分离。

预测层可以为全部自选股输出：

- 相对排名；
- A/B/C/D 等级；
- 数据充分性；
- 主要正负特征；
- 模型版本；
- 校准状态。

未校准阶段：

```text
probability_calibrated = false
numeric_probability = null
```

规则分数不是上涨概率。排名第一也可能没有正式买点。

## 23. 概率事件

概率必须针对可验证事件，而不是模糊的“次日上涨”：

```text
net_positive_by_1430 =
    下一交易日 14:30 前存在正常可执行退出，
    且相对冻结的实际或理论入场价，
    按标准退出协议计算的手续费后收益 > 0

first_zone_before_protection =
    第一目标区先于保护位出现

protection_before_first_zone =
    保护位先于第一目标区出现

exit_unavailable =
    计划窗口内无法正常退出
```

观察对象与模型预测必须分开标识：

```text
observation_id =
    trade_date | code | primary_channel

prediction_id =
    observation_id | model_version
```

同一股票同一交易日同一主通道只生成一个观察对象。不同模型可对同一观察对象生成
独立预测，但共享同一个实际结果。后续检查点只追加状态，不重复计算观察样本。

## 24. 影子样本

空仓不妨碍模型核对。所有在 14:35 前满足背景和确认、且理论上可执行的影子候选
都可以进入研究账本：

```text
selected_as_final = true | false
production_buyable = false
diagnostic_only = false
```

影子样本的买区、保护位和目标区必须使用与正式策略相同的冻结公式。不得为了获得
非空样本手工指定价格，也不得要求真实买入。

缺少生产字段、盘后重建或事后选择的样本只能进入诊断统计，不能进入概率训练。

## 25. 校准门槛

建议分级：

- 3 个交易日且累计 20 个样本：只检查流程和字段；
- 至少 10 个交易日且每通道 50 个样本：评估影子规则；
- 每通道至少 150 个样本、至少 30 个交易日、覆盖不少于 3 种市场环境：
  才允许概率训练和校准；
- 每个环境至少 30 个样本；
- 第一目标先发生和保护位先发生均至少 30 例；
- 叶子事件不足时合并为更粗路径；
- 稀有 `exit_unavailable` 使用压力情景，不输出伪精确概率。

概率输出必须包含：

- `model_id`；
- `training_cutoff`；
- 样本量；
- 校准方法；
- 基准率；
- 置信区间；
- Brier 分数或同类校准指标。

## 26. 参数变更

不得因单只股票或单日结果调整阈值。

参数挑战者至少满足：

- 同类问题持续不少于 3 个交易日；
- 累计不少于 20 个可执行评分样本；
- 只修改一个参数组；
- 使用时间序列走步验证；
- 同时比较目标命中、手续费后收益、保护位先发生率和不可退出风险；
- 挑战者先影子运行，审批后才可进入生产。

## 27. 不可变证据

每个生产检查点建议保存：

```json
{
  "decision_at": "ISO-8601",
  "checkpoint": "14:45",
  "watchlist_version": "example-v1",
  "strategy_version": "strategy-v1",
  "model_version": "shadow-v1",
  "input_manifest_hash": "sha256",
  "market_environment": {},
  "watchlist_status": [],
  "formal_candidate": null,
  "conditional_watches": [],
  "next_day_exit_protocol": {},
  "previous_checkpoint_hash": "sha256",
  "record_hash": "sha256"
}
```

保存后不得覆盖。任何更正都以追加事件形式记录。

## 28. 标准输出

每次分析按以下顺序输出：

1. 市场和行业环境；
2. 自选池完整状态表；
3. 全部股票的预测等级和校准状态；
4. 最多一个预测首选；
5. 最多一个正式候选，或明确空仓；
6. 最多两个条件观察；
7. 其余股票的排除原因和转强条件；
8. 相对上一检查点变化；
9. 正式候选的买区、禁追价、保护位和目标区；
10. 次日退出协议；
11. 数据缺口、模型版本和风险声明。

## 29. MarketBase 请求示例

以下示例只请求客观数据，不包含策略判断：

```json
{
  "schema_version": 1,
  "request_id": "watchlist-checkpoint-YYYYMMDD-1445",
  "codes": ["000001", "600000"],
  "daily": {
    "lookback": 120,
    "fields": ["raw", "ma", "rsi", "macd", "atr"]
  },
  "minute": {
    "date": "YYYY-MM-DD",
    "start": "13:00",
    "end": "14:45",
    "fields": ["raw", "vwap"]
  }
}
```

行业实时快照、事件证据、证券交易规则或账户费用若不在当前 MarketBase 交接中，
必须由独立、版本化的客观数据适配器提供。缺失时策略降级，不得在 MarketBase 内
硬编码结论。

## 30. 风险声明

该策略规范用于研究和决策支持，不构成收益承诺。公开数据可能延迟、缺失、限流或
存在口径差异。A 股 T+1、涨跌停、停牌、流动性和跳空会使实际损失超过计划保护位。
任何实现均不得自动登录券商、索取账户密码或未经明确授权自动下单。

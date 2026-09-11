r"""T1 收盘终判计算脚本（2026-09-10 15:30 轮）
只读计算：读取昨日(9/9)扫描 CSV + 今日(9/10)收盘快照，输出逐只终判、双口径模拟收益与汇总统计。
策略裁决规则见 D:\Deepseek\05_脚本区\V08241039_T1盘中扫描定时任务\strategy_charter.md
"""
import io
import sys
from pathlib import Path

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MB = Path(r"D:\Environment\marketbase")
FAST = MB / "data" / "cache" / "fast"

prev = pd.read_csv(FAST / "scan_result_20260909_1606.csv", dtype={"code": str}, encoding="utf-8-sig")
snap = pd.read_csv(FAST / "snapshot_latest.csv", dtype={"code": str}, encoding="utf-8-sig")

print(f"prev_scan rows={len(prev)}  snapshot rows={len(snap)}  observed_at={snap['observed_at'].iloc[0]}")

med_series = pd.to_numeric(snap["change_pct"], errors="coerce")
MED = med_series.median()
UP = (med_series > 0).mean() * 100
print(f"全市场中位数涨跌幅: {MED:+.3f}%  上涨占比: {UP:.1f}%")

snap_cols = snap[["code", "name", "open", "high", "low", "price", "pre_close", "change_pct", "volume"]]\
    .rename(columns={"name": "name_today", "volume": "snap_volume"})

prev["code"] = prev["code"].str.zfill(6)
snap_cols_code = snap_cols.copy()
snap_cols_code["code"] = snap_cols_code["code"].str.zfill(6)

official = prev[prev["tail_risk_count"] == 0].copy()
watch = prev[prev["tail_risk_count"] > 0].copy()
print(f"昨日候选 {len(prev)} 只 | 正式名单(tail_risk_count==0) {len(official)} 只 | 条件观察 {len(watch)} 只")

# 宇宙合规复核（v2.1：60/00 开头，现价>50）——昨日 CSV 原值检查
for r in prev.itertuples():
    issues = []
    if not (r.code.startswith("60") or r.code.startswith("00")):
        issues.append("非沪/深主板")
    if r.price <= 50:
        issues.append("现价≤50")
    if issues:
        print(f"宇宙异常: {r.code} {r.name} price={r.price} {issues}")

official_sorted = official.sort_values(["opportunity_score", "rr_ratio"], ascending=[False, False])
TOP5 = official_sorted.head(5)
print("昨日正式名单按机会分降序：")
for i, r in enumerate(official_sorted.itertuples(), 1):
    tag = " <= TOP5" if i <= 5 else ""
    print(f"  {i}. {r.code} {r.name} score={r.opportunity_score} rr={r.rr_ratio} tail={r.tail_risk_count}{tag}")


def judge(r, label):
    """对单只输出标签 + A口径 + V5口径模拟收益"""
    o, h, l, c = r.open_t, r.high_t, r.low_t, r.price_t
    bl, bh = r.buy_low, r.buy_high
    cl, pr = r.chase_line, r.protect
    s1l = r.sell1_low
    in_buy = (l <= bh) and (h >= bl)
    hit_sell1 = h >= s1l
    broke = l < pr
    gap_pct = (o / bh - 1) * 100
    cant_buy = (o > bh) and (l > bh)

    # 标签（四选一）
    if broke and in_buy:
        tag = "失败"
    elif cant_buy:
        tag = "买不到"
    elif not in_buy:
        tag = "未触发"
    else:
        # 入区未破保护：成功要求达卖一或收盘>=入场
        entry_ref = bh if o > bh else o  # 成功判定参考入场价：预挂/回踩按 buy_high，跳空追入按开盘价
        ok = hit_sell1 or (c >= entry_ref)
        tag = "成功" if ok else "失败"
    # 修正：入区且破保护即失败（即便收盘拉回）——按提示词定义
    if in_buy and broke:
        tag = "失败"

    # A 口径：buy_high 限价，出场 保护>卖一>收盘；未触发/买不到不计
    a_ret = None
    a_entry = a_exit = None
    if in_buy:
        a_entry = bh
        if broke:
            a_exit = pr
        elif hit_sell1:
            a_exit = s1l
        else:
            a_exit = c
        a_ret = (a_exit / a_entry - 1) * 100

    # V5 口径
    v5_ret = None
    v5_entry = v5_exit = None
    v5_mode = None
    if o <= bh:
        v5_mode = "预挂成交"
        v5_entry = o
    elif o <= bh * 1.01 and o <= cl:
        v5_mode = "开盘追买"
        v5_entry = o
    elif l <= bh:
        v5_mode = "回踩接回"
        v5_entry = bh
    if v5_entry is not None:
        tp = min(s1l, v5_entry * 1.03)
        # 触及判定：tp>=入场 → 高点升至 tp 即触及；tp<入场（退化）→ 价格回落至 tp 才触及
        tp_touched = (h >= tp) if tp >= v5_entry else (l <= tp)
        if broke:
            # 盘中破保护位 -> 保护位离场（保守：保护优先）
            v5_exit = pr
        elif tp_touched:
            v5_exit = tp
        else:
            v5_exit = c
        v5_ret = (v5_exit / v5_entry - 1) * 100
    else:
        v5_mode = "放弃(买不到)"

    print(f"[{label}] {r.code} {r.name} score={r.opportunity_score} | "
          f"O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} | 买区[{bl:.2f},{bh:.2f}] "
          f"卖一低={s1l:.2f} 保护={pr:.2f} 禁追={cl:.2f} | gap={gap_pct:+.2f}% | "
          f"标签={tag} | A: {a_ret if a_ret is None else round(a_ret,2)}% | "
          f"V5[{v5_mode}]: {v5_ret if v5_ret is None else round(v5_ret,2)}%")
    return dict(code=r.code, name=r.name, score=r.opportunity_score, tag=tag,
                in_buy=in_buy, hit_sell1=hit_sell1, broke=broke, gap_pct=gap_pct,
                a_ret=a_ret, a_entry=a_entry, a_exit=a_exit,
                v5_ret=v5_ret, v5_entry=v5_entry, v5_exit=v5_exit, v5_mode=v5_mode,
                o=o, h=h, l=l, c=c, bl=bl, bh=bh, s1l=s1l, pr=pr, cl=cl,
                rr=r.rr_ratio, s1h=r.sell1_high, s2l=r.sell2_low, s2h=r.sell2_high,
                tail=r.tail_risk_count, buy_low=bl, buy_high=bh)


def run(df, label):
    sc = snap_cols_code.rename(columns={"open": "open_t", "high": "high_t", "low": "low_t",
                                        "price": "price_t", "change_pct": "chg_t"})
    m = df.merge(sc, on="code", how="left")
    missing = m[m["open_t"].isna()]
    for r in missing.itertuples():
        print(f"  [{label}] 快照缺失: {r.code} {r.name}")
    m = m.dropna(subset=["open_t", "high_t", "low_t", "price_t"]).copy()
    halted = m[(pd.to_numeric(m["snap_volume"], errors="coerce").fillna(0) <= 0) | (m["price_t"] <= 0)]
    for r in halted.itertuples():
        print(f"  [{label}] 停牌/无成交剔除: {r.code} {r.name}")
    m = m[~m.index.isin(halted.index)].copy()
    rows = [judge(r, label) for r in m.itertuples()]
    return pd.DataFrame(rows)


print("\n===== TOP5 逐只终判 =====")
top5_res = run(TOP5, "TOP5")
print("\n===== 正式名单(全部) =====")
off_res = run(official, "正式")
print("\n===== 条件观察名单(全部) =====")
watch_res = run(watch, "观察")


def summarize(v, label):
    n = len(v)
    if n == 0:
        print(f"{label}: 0 只")
        return
    counts = v["tag"].value_counts().to_dict()
    a = v["a_ret"].dropna()
    v5 = v["v5_ret"].dropna()
    a_avg = a.mean() if len(a) else None
    v5_avg = v5.mean() if len(v5) else None
    print(f"{label}: n={n} 标签计数={counts} | A口径均值={None if a_avg is None else round(a_avg,2)}% "
          f"(n={len(a)}) | V5口径均值={None if v5_avg is None else round(v5_avg,2)}% (n={len(v5)})")
    # 高分破保护
    for r in v[(v["broke"]) & (v["score"] >= 70)].itertuples():
        print(f"  !! 高分(>=70)破保护: {r.code} {r.name} score={r.score}")


print("\n===== 汇总 =====")
summarize(top5_res, "TOP5")
summarize(off_res, "正式名单")
summarize(watch_res, "条件观察")

# 全名单收益（辅助参考，A 与 V5 分别）
all_res = pd.concat([off_res, watch_res])
a_all = all_res["a_ret"].dropna()
v5_all = all_res["v5_ret"].dropna()
print(f"全名单 A口径均值={a_all.mean():.2f}%(n={len(a_all)})  V5口径均值={v5_all.mean():.2f}%(n={len(v5_all)})")

# TOP1 基准：昨日 TOP1 = 松发股份 603268，今日开盘买、收盘卖
top1 = top5_res.iloc[0]
top1_ret = (top1["c"] / top1["o"] - 1) * 100
print(f"\nTOP1基准: {top1['code']} {top1['name']} 今开{top1['o']:.2f}买入 今收{top1['c']:.2f}卖出 = {top1_ret:+.2f}%")

# TOP5 均值
t5_a = top5_res["a_ret"].dropna().mean()
t5_v5 = top5_res["v5_ret"].dropna().mean()
print(f"TOP5 A口径均值={t5_a:+.2f}%  V5口径均值={t5_v5:+.2f}%  中位数={MED:+.2f}%")
print(f"方向结论: A口径{'有效' if t5_a > MED else '失效'}  V5口径{'有效' if t5_v5 > MED else '失效'}")

# 持仓股收盘复核数据
print("\n===== 持仓股 603019 收盘数据 =====")
row = snap_cols_code[snap_cols_code["code"] == "603019"]
if len(row):
    r0 = row.iloc[0]
    print(f"O={r0['open']} H={r0['high']} L={r0['low']} C={r0['price']} pre_close={r0['pre_close']} chg={r0['change_pct']}%")
else:
    print("快照中未找到 603019")

# BYD 轮动信号收盘口径
print("\n===== BYD 002594 收盘 =====")
row = snap_cols_code[snap_cols_code["code"] == "002594"]
if len(row):
    r0 = row.iloc[0]
    print(f"C={r0['price']} pre_close={r0['pre_close']} chg={r0['change_pct']}%")
else:
    print("快照中未找到 002594")

# 输出详细表格供报告使用
print("\n===== TOP5 明细表 =====")
print(top5_res[["code", "name", "score", "tag", "gap_pct", "a_entry", "a_exit", "a_ret",
                "v5_mode", "v5_entry", "v5_exit", "v5_ret", "o", "h", "l", "c"]].to_string(index=False))

print("\n===== 正式+观察全名单明细（供报告文件） =====")
print(all_res[["code", "name", "score", "tag", "tail", "a_ret", "v5_ret", "v5_mode"]].to_string(index=False))

# -*- coding: utf-8 -*-
"""2026-09-09 收盘终判轮：昨日(9/8) TOP5 + 全名单 双口径终判计算。
数据源：scan_result_20260908_1536.csv × snapshot_latest.csv(9/9收盘口径, observed_at 16:06)。
口径：prompt_verdict 15:30 轮（总纲 V5 §3.2/§10 双口径 A/V5 + TOP1 基准）。"""
import io
import sys
from pathlib import Path

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

MB = Path(r"D:\Environment\marketbase")
FAST = MB / "data" / "cache" / "fast"

prev = pd.read_csv(FAST / "scan_result_20260908_1536.csv", dtype={"code": str}, encoding="utf-8-sig")
snap = pd.read_csv(FAST / "snapshot_latest.csv", dtype={"code": str}, encoding="utf-8-sig")
snap_cols = snap[["code", "open", "high", "low", "price", "pre_close", "change_pct"]].rename(
    columns={"open": "t_open", "high": "t_high", "low": "t_low", "price": "t_close",
             "pre_close": "t_pre_close", "change_pct": "t_chg"}).copy()

print(f"快照 observed_at: {snap['observed_at'].iloc[0]} | quote_time: {snap['quote_time'].iloc[0]} | 覆盖 {len(snap)} 只")
med = pd.to_numeric(snap["change_pct"], errors="coerce").median()
up = (pd.to_numeric(snap["change_pct"], errors="coerce") > 0).sum()
down = (pd.to_numeric(snap["change_pct"], errors="coerce") < 0).sum()
flat = (pd.to_numeric(snap["change_pct"], errors="coerce") == 0).sum()
total = len(snap)
print(f"今日全市场中位数涨跌幅: {med:+.4f}% | 上涨 {up}/{total} = {up/total*100:.1f}% (跌 {down} / 平 {flat})")

official = prev[prev["tail_risk_count"] == 0].copy()
watch = prev[prev["tail_risk_count"] > 0].copy()
print(f"\n昨日(9/8)扫描: 全部 {len(prev)} | 正式(tail=0) {len(official)} | 条件观察(tail>0) {len(watch)}")

official["code"] = official["code"].str.zfill(6)
watch["code"] = watch["code"].str.zfill(6)
official = official.sort_values(["opportunity_score", "rr_ratio"], ascending=[False, False]).reset_index(drop=True)
watch = watch.sort_values(["opportunity_score", "rr_ratio"], ascending=[False, False]).reset_index(drop=True)
top5 = official.head(5).copy()
top1 = official.iloc[0]


def judge(r):
    """返回 dict: 标签 + A口径 + V5口径入场/出场/收益"""
    o, h, l, c = r.t_open, r.t_high, r.t_low, r.t_close
    bl, bh, prot = r.buy_low, r.buy_high, r.protect
    s1l, s2l, chase = r.sell1_low, r.sell2_low, r.chase_line
    out = {}

    # ---- 标签（四选一） ----
    in_buy = (l <= bh) and (h >= bl)
    broke = l < prot
    hit_s1 = h >= s1l
    if in_buy and (not broke) and (hit_s1 or c >= bh):
        label = "成功"
    elif in_buy and broke:
        label = "失败"
    elif (o > bh) and (l > bh):
        label = "买不到"
    elif not in_buy and (l > bh):
        label = "未触发"
    else:
        # 入区/未破保护/未达卖一/收盘<入场价 → 历史既定保守口径计失败（9/2、9/7 先例）
        label = "失败"

    # ---- A 口径（影子对照 V4）----
    a_entry = a_exit = a_ret = None
    if in_buy:
        a_entry = bh
        if broke:
            a_exit = prot
        elif hit_s1:
            a_exit = s1l
        else:
            a_exit = c
        a_ret = a_exit / a_entry - 1.0

    # ---- V5 口径（现行）----
    v_entry = v_exit = v_ret = None
    v_mode = ""
    gap_pct = (o - bh) / bh  # 开盘相对买区上沿跳空
    if o > bh and o <= bh * 1.01 and o <= chase:
        # 跳空≤1%且不越禁追线 → 开盘价追入
        v_entry = o
        v_mode = f"跳空追买{gap_pct*100:+.2f}%"
    elif in_buy:
        v_entry = bh
        v_mode = "买区限价"
    if v_entry is not None:
        near_tp = min(s1l, v_entry * 1.03)
        if broke and l < prot:
            # 盘中破保护 → 保护位离场（优先）
            v_exit = prot
        elif h >= near_tp:
            # 盘中触及近止盈 → 即卖
            v_exit = near_tp
        else:
            v_exit = c
        v_ret = v_exit / v_entry - 1.0

    # ---- 附加事实 ----
    out.update(label=label, in_buy=in_buy, broke=broke, hit_s1=hit_s1,
               a_entry=a_entry, a_exit=a_exit, a_ret=a_ret,
               v_entry=v_entry, v_exit=v_exit, v_ret=v_ret, v_mode=v_mode,
               t_open=o, t_high=h, t_low=l, t_close=c, t_chg=r.t_chg,
               gap_pct=gap_pct * 100)
    return out


def run(df, label):
    m = df.merge(snap_cols, on="code", how="left")
    missing = m[m["t_high"].isna()]
    if len(missing):
        print(f"\n[{label}] 今日快照缺失: " + ", ".join(f"{r.code}({r.name})" for r in missing.itertuples()))
    m = m.dropna(subset=["t_high", "t_low", "t_close"]).reset_index(drop=True)
    res = [judge(r) for r in m.itertuples()]
    for i, rr in enumerate(res):
        m.loc[i, "label"] = rr["label"]
        m.loc[i, "a_ret"] = rr["a_ret"]
        m.loc[i, "v_ret"] = rr["v_ret"]
        m.loc[i, "a_entry"] = rr["a_entry"]
        m.loc[i, "a_exit"] = rr["a_exit"]
        m.loc[i, "v_entry"] = rr["v_entry"]
        m.loc[i, "v_exit"] = rr["v_exit"]
        m.loc[i, "v_mode"] = rr["v_mode"]
        m.loc[i, "in_buy"] = rr["in_buy"]
    print(f"\n===== {label} 汇总 =====")
    print("标签计数:", m["label"].value_counts().to_dict())
    sub_a = m.dropna(subset=["a_ret"])
    sub_v = m.dropna(subset=["v_ret"])
    a_mean = sub_a["a_ret"].mean() * 100 if len(sub_a) else float("nan")
    v_mean = sub_v["v_ret"].mean() * 100 if len(sub_v) else float("nan")
    print(f"A口径入区 {len(sub_a)} 只 均值 {a_mean:+.2f}% | V5口径交易 {len(sub_v)} 只 均值 {v_mean:+.2f}%")
    return m, a_mean, v_mean


m_top5, top5_a, top5_v = run(top5, "昨日TOP5(正式按机会分降序前5)")
m_off, off_a, off_v = run(official, "正式名单全部")
m_watch, watch_a, watch_v = run(watch, "条件观察名单全部")

cols = ["code", "name", "opportunity_score", "label", "t_open", "t_high", "t_low", "t_close", "t_chg",
        "buy_low", "buy_high", "chase_line", "protect", "sell1_low", "sell1_high", "sell2_low", "sell2_high", "rr_ratio",
        "a_entry", "a_exit", "a_ret", "v_entry", "v_exit", "v_ret", "v_mode"]
pd.set_option("display.width", 300)
pd.set_option("display.max_columns", 40)
pd.set_option("display.float_format", lambda x: f"{x:.2f}")

print("\n===== 昨日TOP5 逐只明细 =====")
print(m_top5[cols].to_string(index=False))

print("\n===== 正式名单其余明细 =====")
print(m_off.iloc[5:][cols].to_string(index=False))

print("\n===== 条件观察名单明细 =====")
print(m_watch[cols].to_string(index=False))

# ---- TOP1 基准 ----
r1 = top1.merge(snap_cols, on="code", how="left").iloc[0]
top1_ret = (r1["t_close"] / r1["t_open"] - 1.0) * 100
print(f"\n===== TOP1 基准 =====")
print(f"TOP1: {r1['code']} {r1['name']} 机会分 {r1['opportunity_score']} | 今日 O {r1['t_open']:.2f} C {r1['t_close']:.2f} | 开盘买收盘卖: {top1_ret:+.2f}%")

# ---- 汇总输出 ----
print(f"\n===== 汇总 =====")
print(f"昨日候选数(正式): {len(official)} | 条件观察: {len(watch)}")
print(f"TOP5: A口径 {top5_a:+.2f}% (n={int(m_top5['a_ret'].notna().sum())}) | V5口径 {top5_v:+.2f}% (n={int(m_top5['v_ret'].notna().sum())})")
print(f"正式全名单: A {off_a:+.2f}% | V5 {off_v:+.2f}%")
print(f"条件观察: A {watch_a:+.2f}% | V5 {watch_v:+.2f}%")
print(f"TOP1基准: {top1_ret:+.2f}%")
print(f"全市场中位数: {med:+.2f}%")

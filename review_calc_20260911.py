# -*- coding: utf-8 -*-
"""2026-09-11 复盘轮验证计算（只读数据、无策略修改）。纯 csv 模块。
数据源：昨日扫描 scan_result_20260910_1531.csv × 今日快照 snapshot_latest.csv（14:02 盘中口径）。
口径：开盘三档判定（V5 §3.2）+ A 口径模拟（buy_high 限价介入，出场保守优先级 保护位 > 卖一 > 现价）。
"""
import io
import sys
import csv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

FAST = r"D:\Environment\marketbase\data\cache\fast"

# 读昨日扫描
prev = []
with open(FAST + r"\scan_result_20260910_1531.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        prev.append(r)

# 读今日快照（建 dict）
snap = {}
with open(FAST + r"\snapshot_latest.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        snap[r["code"]] = r

print(f"昨日扫描 20260910_1531: {len(prev)} 只候选")
official = [r for r in prev if int(r["tail_risk_count"]) == 0]
watch = [r for r in prev if int(r["tail_risk_count"]) > 0]
print(f"正式名单 {len(official)} 只 | 条件观察 {len(watch)} 只")

# TOP5：机会分降序，同分按 RR 降序
official.sort(key=lambda r: (-int(r["opportunity_score"]), -float(r["rr_ratio"])))
top5 = official[:5]
print("TOP5:", [(r["code"], r["name"], r["opportunity_score"]) for r in top5])

print("\n=== 正式名单 TOP5 逐只（开盘口径，盘中 14:02 现价兜底） ===")
sum_a = sum_v = sum_n = 0.0
n_cnt = 0
for r in top5:
    code = r["code"]
    s = snap.get(code)
    if s is None:
        print(f"{code} {r['name']}: 快照缺失！")
        continue
    o, h, l, p = float(s["open"]), float(s["high"]), float(s["low"]), float(s["price"])
    bl, bh = float(r["buy_low"]), float(r["buy_high"])
    cl, pt = float(r["chase_line"]), float(r["protect"])
    s1l, s1h = float(r["sell1_low"]), float(r["sell1_high"])
    s2l = float(r["sell2_low"])
    chg = float(s["change_pct"])

    # V5 三档开盘判定
    if o <= bh:
        mode, entry = "预挂成交", o
    elif o <= bh * 1.01 and o <= cl:
        mode, entry = "V5追买", o
    else:
        mode, entry = "跳空放弃(可回踩接回)", bh
    gap_pct = (o / bh - 1) * 100

    # A 口径：buy_high 限价介入，保守优先级
    in_buy = (l <= bh) and (h >= bl)
    hit_s1 = h >= s1l
    broke = l < pt
    ret_a = None
    if in_buy:
        e = bh
        if broke:
            ret_a = pt / e - 1.0
        elif hit_s1:
            ret_a = s1l / e - 1.0
        else:
            ret_a = p / e - 1.0

    # V5 口径：按 mode 入场；出场 = 破保护 > 近止盈 > 现价
    ret_v5 = None
    exit_v5 = None
    tp = min(s1l, entry * 1.03)
    if mode != "跳空放弃(可回踩接回)":
        if broke:
            exit_v5 = pt
        elif tp >= entry:
            exit_v5 = tp if h >= tp else p
        else:  # 退化结构：回落至 tp 才算触及
            exit_v5 = tp if l <= tp else p
        ret_v5 = exit_v5 / entry - 1.0
    ret_now = p / entry - 1.0 if mode != "跳空放弃(可回踩接回)" else None

    if ret_a is not None:
        sum_a += ret_a
    if ret_v5 is not None:
        sum_v5 = ret_v5
    else:
        sum_v5 = 0.0
    if ret_now is not None:
        sum_n += ret_now
        n_cnt += 1
    globals()['sum_v5_acc'] = globals().get('sum_v5_acc', 0.0) + (ret_v5 if ret_v5 is not None else 0.0)
    globals()['v5_cnt'] = globals().get('v5_cnt', 0) + (1 if ret_v5 is not None else 0)

    fmt = lambda x: f"{x*100:+.2f}%" if x is not None else "-"
    print(f"{code} {r['name']} {r['opportunity_score']}分 | 开{o:.2f}(跳空{gap_pct:+.2f}%) 高{h:.2f} 低{l:.2f} 现{p:.2f}({chg:+.2f}%) | "
          f"买区[{bl:.2f},{bh:.2f}] 卖一[{s1l:.2f},{s1h:.2f}] 卖二≥{s2l:.2f} 保护{pt:.2f} 禁追{cl:.2f} RR{r['rr_ratio']} | "
          f"档位={mode} 入场{entry:.2f} 近止盈线{tp:.2f} | 入区{in_buy} 达卖一{hit_s1} 破保护{broke} | "
          f"A:{fmt(ret_a)} V5:{fmt(ret_v5)} 现价口径:{fmt(ret_now)}")

print(f"\nTOP5 均值: A口径 {sum_a/len(top5)*100:+.2f}% | V5口径 {globals().get('sum_v5_acc',0)/max(globals().get('v5_cnt',1),1)*100:+.2f}% | 现价口径(开盘入现价出) {sum_n/max(n_cnt,1)*100:+.2f}%")

# 全名单汇总计数
print("\n=== 全名单汇总（只计数） ===")
for label, df in [("正式", official), ("观察", watch), ("合计", prev)]:
    inb = hits = brk = miss = 0
    for r in df:
        s = snap.get(r["code"])
        if s is None:
            miss += 1
            continue
        h_, l_ = float(s["high"]), float(s["low"])
        if (l_ <= float(r["buy_high"])) and (h_ >= float(r["buy_low"])):
            inb += 1
        if h_ >= float(r["sell1_low"]):
            hits += 1
        if l_ < float(r["protect"]):
            brk += 1
    print(f"{label}({len(df)}只,快照缺失{miss}): 入买区 {inb} | 达卖一 {hits} | 破保护 {brk}")

# 正式名单其余标的（第 6 名以后不存在，正式共 4 只全在 TOP5）
# TOP1 基准（盘中参考）
t1 = top5[0]
s1 = snap[t1["code"]]
o1, p1 = float(s1["open"]), float(s1["price"])
print(f"\nTOP1 基准(盘中参考): {t1['name']} 开盘 {o1:.2f} → 现价 {p1:.2f} = {(p1/o1-1)*100:+.2f}%")

# 全市场中位数与上涨占比
chgs = []
for r in snap.values():
    try:
        c = float(r["change_pct"])
        chgs.append(c)
    except (ValueError, TypeError):
        pass
chgs.sort()
n = len(chgs)
med = chgs[n//2] if n % 2 == 1 else (chgs[n//2-1]+chgs[n//2])/2
up = sum(1 for c in chgs if c > 0)
print(f"全市场: n={n} 中位数 {med:+.3f}% | 上涨占比 {up/n*100:.2f}% (弱市<45%/正常/强势>60%)")

# 持仓 603019
s = snap.get("603019")
print(f"\n持仓 603019 中科曙光: 昨收{s['pre_close']} 开{s['open']} 高{s['high']} 低{s['low']} 现{s['price']}({s['change_pct']}%) quote_time={s['quote_time']}")
# BYD
s = snap.get("002594")
print(f"BYD 002594: 昨收{s['pre_close']} 开{s['open']} 高{s['high']} 低{s['low']} 现{s['price']}({s['change_pct']}%)")

# -*- coding: utf-8 -*-
"""T1 月度追踪表写入（2026-09-10 收盘终判轮，唯一写入者）
幂等：2026-09-10 已存在则重算覆盖该行，不追加。13 列 V5 格式，整行双引号包裹。
"""
import csv
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

CSV_PATH = Path(r"D:\Deepseek\02_正式交付区\T1复盘追踪_202609.csv")
HEADER = ["日期", "昨日候选数", "成功", "失败", "未触发", "买不到",
          "V5模拟收益%", "TOP1基准%", "正式模拟收益%", "条件观察模拟收益%",
          "全市场中位数%", "方向结论", "备注"]

# ---- 本日终判结果（来自 verdict_calc_20260910.py 收盘口径计算）----
DATE = "2026-09-10"
row = {
    "日期": "2026-09-10",
    "昨日候选数": "23",
    "成功": "16",          # 正式6 + 观察10
    "失败": "7",           # 正式2 + 观察5
    "未触发": "0",
    "买不到": "0",
    "V5模拟收益%": "-0.82",   # 昨日TOP5 V5口径均值
    "TOP1基准%": "+1.83",     # 松发股份 开196.00 买 收199.59 卖
    "正式模拟收益%": "-0.66", # 正式名单8只 A口径均值
    "条件观察模拟收益%": "-0.84",  # 观察名单15只 A口径均值
    "全市场中位数%": "-1.49",
    "方向结论": "有效",
    "备注": ("收盘终判(9/10,快照observed_at=15:31:42收盘口径重采);昨日候选=9/9 16:06盘后扫描23只"
             "(正式8+观察15);TOP5:A口径-1.01%/V5口径-0.82%均跑赢中位-1.49%有效;V5优于A0.19pp"
             "(松发预挂0.12%vs A-0.33%/移远近止盈+3.00%vs A-0.25%/万华追买-1.15%拖累);"
             "征和工业(65分TOP5)破保护A-4.29%;景旺电子观察V5近止盈退化结构tp104.54<入场108.88按回落触及计+1.04%;"
             "汇顶科技(45分)收盘51.00<入场51.59判失败;BYD 9/10收盘-2.75%触发次日半导体轮动关注信号;"
             "持仓603019收盘82.35低于保护位84.90(台账只读)"),
}

# ---- 读取现有表 ----
if CSV_PATH.exists():
    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    header = rows[0] if rows else HEADER
    data = rows[1:]
else:
    header = HEADER
    data = []

# 表头校验/迁移（旧11列V4 -> 13列V5）
if header[:6] != HEADER[:6] or len(header) != 13:
    print(f"表头非13列V5格式(实际{len(header)}列): {header}")
    if len(header) == 11:
        print("检测到旧11列V4表头，执行迁移...")
        new_header = header[:6] + ["V5模拟收益%", "TOP1基准%"] + header[6:]
        new_data = []
        for r in data:
            r = (csv.reader([r]) if False else [c.strip().strip('"') for c in r])
            new_data.append(r[:6] + ["未启用", "未启用"] + r[6:])
        header, data = new_header, new_data
    else:
        # 保持原样，按原列数写出（尽力而为）
        print("警告: 表头列数异常，按原样保留")

# ---- 查重（幂等）----
existing_dates = [r[0].strip().strip('"') for r in data if r]
if DATE in existing_dates:
    idx = existing_dates.index(DATE)
    print(f"本日 {DATE} 已存在(第{idx+2}行)，重算覆盖")
    data[idx] = [row[h] for h in header]
    action = "覆盖"
else:
    data.append([row[h] for h in header])
    action = "追加"

# ---- 写回（整行双引号包裹，UTF-8 BOM）----
with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, quoting=csv.QUOTE_ALL)
    w.writerow(header)
    for r in data:
        w.writerow(r)

print(f"已{action}写入: {CSV_PATH}")
print(f"当前表共 {len(data)} 个交易日行")

# ---- 累计统计 ----
print("\n===== 累计统计 =====")
v5_vals, a_vals, top1_vals, dates = [], [], [], []
for r in data:
    cells = [c.strip().strip('"') for c in r]
    d = cells[0]
    dates.append(d)
    def parse(v):
        v = v.strip().strip('"')
        if v in ("", "未启用", "无入区", "无成功"):
            return None
        try:
            return float(v.replace("+", "").replace("%", ""))
        except ValueError:
            return None
    v5_vals.append((d, parse(cells[6]) if len(cells) > 6 else None))
    top1_vals.append((d, parse(cells[7]) if len(cells) > 7 else None))
    a_vals.append((d, parse(cells[8]) if len(cells) > 8 else None))

# A口径累计（正式模拟收益列）
a_valid = [(d, v) for d, v in a_vals if v is not None]
print(f"A口径(正式模拟收益列): 有效 {len(a_valid)} 天 / 总 {len(data)} 天, 累计均值 {sum(v for _, v in a_valid)/len(a_valid):+.2f}%")

# V5口径累计
v5_valid = [(d, v) for d, v in v5_vals if v is not None]
if v5_valid:
    print(f"V5口径(V5模拟收益列): 有效 {len(v5_valid)} 天 / 总 {len(data)} 天, 累计均值 {sum(v for _, v in v5_valid)/len(v5_valid):+.2f}%")

# TOP1基准累计
t1_valid = [(d, v) for d, v in top1_vals if v is not None]
if t1_valid:
    print(f"TOP1基准: 有效 {len(t1_valid)} 天, 累计均值 {sum(v for _, v in t1_valid)/len(t1_valid):+.2f}%")

# 近5日滚动（含今日，取最后5个有数据的交易日）
last5 = [(d, v) for d, v in a_vals if v is not None][-5:]
print(f"\n近5日(正式模拟收益A口径): " + " | ".join(f"{d}:{v:+.2f}%" for d, v in last5))
last5_v5 = [(d, v) for d, v in v5_vals if v is not None][-5:]
if last5_v5:
    print(f"近5日(V5口径): " + " | ".join(f"{d}:{v:+.2f}%" for d, v in last5_v5))

# V5 vs A vs TOP1 累计对比（仅对两者都有效的日期）
both = [(d, a, v) for (d, a), (_, v) in zip(a_vals, v5_vals) if a is not None and v is not None]
if both:
    diff = [v - a for _, a, v in both]
    print(f"\nV5 vs A 同日对比: {len(both)} 天, V5累计跑赢A {sum(diff):+.2f}pp (均值 {sum(diff)/len(diff):+.2f}pp)")
    t1_map = dict(t1_valid)
    trio = [(d, a, v, t1_map[d]) for d, a, v in both if d in t1_map]
    if trio:
        print(f"V5 vs A vs TOP1 三口径({len(trio)}天): "
              f"A均值 {sum(a for _, a, _, _ in trio)/len(trio):+.2f}% | "
              f"V5均值 {sum(v for _, _, v, _ in trio)/len(trio):+.2f}% | "
              f"TOP1均值 {sum(t for _, _, _, t in trio)/len(trio):+.2f}%")
        v5_vs_t1 = [v - t for _, _, v, t in trio]
        print(f"V5相对TOP1: 累计 {sum(v5_vs_t1):+.2f}pp, 跑输TOP1天数 {sum(1 for x in v5_vs_t1 if x < 0)}/{len(trio)}")

# V5回滚判据：连续5交易日跑输A口径或TOP1基准
if both:
    streak_a = 0
    for d, a, v in reversed(both):
        if v < a:
            streak_a += 1
        else:
            break
    streak_t1 = 0
    t1_map = dict(t1_valid)
    for d, a, v in reversed(both):
        t = t1_map.get(d)
        if t is not None and v < t:
            streak_t1 += 1
        else:
            break
    print(f"\nV5回滚判据状态: 连续跑输A口径 {streak_a} 天 / 连续跑输TOP1基准 {streak_t1} 天 (阈值5)")
    if streak_a >= 5 or streak_t1 >= 5:
        print("!! 触发回滚判据: 列待裁决事项")
    else:
        print("未触发回滚判据")

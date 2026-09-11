# -*- coding: utf-8 -*-
"""交叉验证：用 9/7 扫描 × 9/8 日线缓存重算 9/8 终判，对照追踪表既有行。
目的：验证 verdict_calc_20260909.py 的标签/双口径逻辑与既有口径一致。"""
import csv
import json
import os
import statistics

BASE = r"D:\Environment\marketbase"
YESTERDAY_CSV = os.path.join(BASE, r"data\cache\fast\scan_result_20260907_1534.csv")
DAILY_DIR = os.path.join(BASE, r"data\daily_runs\cache\daily")
TARGET_DATE = "2026-09-08"


def load_daily(code):
    path = os.path.join(DAILY_DIR, code + ".json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for bar in reversed(data.get("rows", [])):
        if str(bar.get("date", "")).startswith(TARGET_DATE):
            return bar
    return None


def f(x, nd=2):
    try:
        return round(float(x), nd)
    except Exception:
        return None


def main():
    with open(YESTERDAY_CSV, "r", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for col in ("opportunity_score", "tail_risk_count", "buy_low", "buy_high", "chase_line",
                    "protect", "sell1_low", "sell1_high", "sell2_low", "sell2_high", "rr_ratio"):
            r[col] = float(r[col])
    formal = [r for r in rows if r["tail_risk_count"] == 0]
    watch = [r for r in rows if r["tail_risk_count"] > 0]
    top5 = sorted(formal, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"]))[:5]
    print(f"9/7扫描: 总{len(rows)} 正式{len(formal)} 观察{len(watch)}")

    def judge(row, touch_mode):
        code = row["code"].zfill(6)
        bar = load_daily(code)
        if bar is None:
            return {"code": code, "name": row["name"], "label": "数据缺失",
                    "A_ret": None, "V5_ret": None, "score": int(row["opportunity_score"])}
        o, h, l, c = (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
        bl, bh, prot, s1l, s1h, chase = (row["buy_low"], row["buy_high"], row["protect"],
                                         row["sell1_low"], row["sell1_high"], row["chase_line"])
        in_zone = (l <= bh) and (h >= bl)
        broke = l < prot
        if touch_mode == "high":
            hit_s1 = h >= s1l
        else:  # zone: 日内区间与卖一区间重叠
            hit_s1 = (h >= s1l) and (l <= s1h)
        if in_zone and broke:
            label = "失败"
        elif in_zone and (hit_s1 or c >= bh):
            label = "成功"
        elif in_zone:
            label = "失败(保守)"
        elif l > bh:
            label = "买不到" if o > bh else "未触发"
        else:
            label = "未触发"
        a_ret = None
        if in_zone:
            if broke:
                a_ret = (prot / bh - 1) * 100
            elif hit_s1:
                a_ret = (s1l / bh - 1) * 100
            else:
                a_ret = (c / bh - 1) * 100
        v5_ret = None
        gap = (o / bh - 1) * 100
        v5_entry = None
        if o > bh and gap <= 1.0 and o <= chase:
            v5_entry = o
        elif in_zone:
            v5_entry = bh
        if v5_entry is not None:
            tgt = min(s1l, v5_entry * 1.03)
            touched = (l <= tgt <= h)
            if l < prot:
                v5_ret = (prot / v5_entry - 1) * 100
            elif touched:
                v5_ret = (tgt / v5_entry - 1) * 100
            else:
                v5_ret = (c / v5_entry - 1) * 100
        return {"code": code, "name": row["name"], "label": label,
                "A_ret": f(a_ret), "V5_ret": f(v5_ret), "score": int(row["opportunity_score"])}

    for mode in ("high", "zone"):
        print(f"\n===== 触及判定: {mode} =====")
        frecs = [judge(r, mode) for r in sorted(formal, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"]))]
        wrecs = [judge(r, mode) for r in sorted(watch, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"]))]
        from collections import Counter
        print("正式 counts:", dict(Counter(x["label"] for x in frecs)))
        print("观察 counts:", dict(Counter(x["label"] for x in wrecs)))
        f_succ = [x["A_ret"] for x in frecs if x["label"] == "成功"]
        w_succ = [x["A_ret"] for x in wrecs if x["label"] == "成功"]
        print("正式成功均值(A):", f(statistics.mean(f_succ)) if f_succ else "无成功")
        print("观察成功均值(A):", f(statistics.mean(w_succ)) if w_succ else "无成功")
        t5a = [x["A_ret"] for x in frecs[:5] if x["A_ret"] is not None]
        t5v = [x["V5_ret"] for x in frecs[:5] if x["V5_ret"] is not None]
        print("TOP5 A 均值:", f(statistics.mean(t5a)) if t5a else None)
        print("TOP5 V5 均值:", f(statistics.mean(t5v)) if t5v else None)
        if mode == "high":
            for x in frecs:
                print(" F", x["code"], x["name"], x["label"], x["A_ret"], x["V5_ret"])


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    with open(r"D:\Environment\marketbase\verdict_validate_0908_out.txt", "w", encoding="utf-8") as fh:
        old = sys.stdout
        sys.stdout = fh
        try:
            main()
        finally:
            sys.stdout = old

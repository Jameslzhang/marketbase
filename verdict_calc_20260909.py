# -*- coding: utf-8 -*-
"""收盘终判辅助脚本（2026-09-09）：昨日 TOP5 + 全名单双口径终判计算。

口径来源：T1 收盘终判任务提示词（15:30 轮）第 2 节 + 总纲 §10/§3.3。
本脚本只做数值计算，不做策略裁决；输出 JSON 供报告引用。
"""
import csv
import json
import os
import statistics

BASE = r"D:\Environment\marketbase"
YESTERDAY_CSV = os.path.join(BASE, r"data\cache\fast\scan_result_20260908_1536.csv")
SNAPSHOT = os.path.join(BASE, r"data\cache\fast\snapshot_latest.csv")
DAILY_DIR = os.path.join(BASE, r"data\daily_runs\cache\daily")

# 2026-09-09 收盘快照（observed_at 16:06，quote_time ≥15:00 收盘口径）中的
# 24 只终判对象今日 OHLC。日线缓存 latest_date 停留在 2026-09-08，
# 故以收盘快照为今日全天事实来源（快照含 open/high/low/price）。
SNAP_TODAY = {
    "600486": (58.88, 59.38, 57.80, 58.42),
    "603605": (61.70, 61.83, 60.10, 60.30),
    "603979": (79.00, 83.02, 79.00, 82.55),
    "605198": (71.09, 77.00, 71.09, 77.00),
    "600309": (77.87, 79.06, 77.11, 78.87),
    "002768": (60.57, 61.46, 59.80, 61.08),
    "000596": (99.98, 100.00, 97.31, 98.21),
    "603338": (58.68, 59.80, 57.71, 59.56),
    "600941": (97.45, 97.45, 96.81, 97.11),
    "600345": (54.01, 56.30, 53.55, 53.97),
    "601318": (55.70, 56.57, 55.57, 56.26),
    "603236": (62.10, 63.66, 60.92, 61.06),
    "603713": (66.99, 67.30, 65.75, 66.72),
    "605123": (80.23, 82.94, 80.12, 82.30),
    "000628": (53.35, 57.80, 53.21, 57.46),
    "601336": (59.88, 60.20, 59.51, 59.93),
    "002779": (82.70, 82.70, 79.70, 80.80),
    "605168": (55.01, 55.37, 49.64, 49.64),
    "603261": (55.67, 57.67, 54.85, 57.60),
    "603444": (402.91, 403.06, 387.02, 387.78),
    "002827": (64.02, 65.66, 62.50, 65.61),
    "603150": (70.45, 71.47, 68.92, 70.14),
    "002837": (63.05, 64.10, 61.93, 62.43),
    "003018": (52.13, 53.77, 50.84, 51.56),
}


def load_daily(code):
    """读取日线缓存。优先返回今日(2026-09-09) bar；若缓存最后一根非今日，
    尝试倒数第二根；否则返回 None（数据缺失，不用旧值补）。"""
    path = os.path.join(DAILY_DIR, code + ".json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows") if isinstance(data, dict) else data
    if not rows:
        return None
    # 从后往前找今日 bar
    for bar in reversed(rows):
        if str(bar.get("date", "")).startswith("2026-09-09"):
            return bar
    return None


def bar_fields(bar):
    """统一抽取 open/high/low/close/date 字段（兼容不同键名）。"""
    def pick(*names, default=None):
        for n in names:
            if isinstance(bar, dict) and n in bar and bar[n] is not None:
                return bar[n]
        return default
    o = pick("open", "o")
    h = pick("high", "h")
    l = pick("low", "l")
    c = pick("close", "c", "price")
    d = pick("date", "day", "trade_date", "time", "d")
    return float(o), float(h), float(l), float(c), d


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
    # TOP5：正式候选按机会分降序，同分按 RR 降序
    top5 = sorted(formal, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"]))[:5]

    out = {"yesterday_file": os.path.basename(YESTERDAY_CSV),
           "yesterday_ts": "2026-09-08 15:37:02",
           "snapshot_observed_at": None,
           "formal_count": len(formal), "watch_count": len(watch),
           "top5": [], "formal_rest": [], "watch": [],
           "top1": None}

    # snapshot observed_at（取第一行）
    with open(SNAPSHOT, "r", encoding="utf-8-sig") as fh:
        snap_rows = list(csv.DictReader(fh))
    out["snapshot_observed_at"] = snap_rows[0].get("observed_at") if snap_rows else None

    # 全市场中位数涨跌幅（今日快照）
    chgs = []
    for s in snap_rows:
        try:
            v = float(s["change_pct"])
        except Exception:
            continue
        chgs.append(v)
    out["market_median_pct"] = f(statistics.median(chgs), 2) if chgs else None
    out["market_up_ratio"] = f(100.0 * sum(1 for v in chgs if v > 0) / len(chgs), 1) if chgs else None
    out["market_n"] = len(chgs)

    def judge(row):
        code = row["code"].zfill(6)
        bar = SNAP_TODAY.get(code)  # (open, high, low, close) 收盘快照口径
        rec = {"code": code, "name": row["name"], "score": int(row["opportunity_score"]),
               "tail_risk": int(row["tail_risk_count"]),
               "buy_low": f(row["buy_low"]), "buy_high": f(row["buy_high"]),
               "chase_line": f(row["chase_line"]), "protect": f(row["protect"]),
               "sell1_low": f(row["sell1_low"]), "sell1_high": f(row["sell1_high"]),
               "sell2_low": f(row["sell2_low"]), "sell2_high": f(row["sell2_high"]),
               "rr": f(row["rr_ratio"])}
        if bar is None:
            rec.update({"daily": None, "label": "数据缺失"})
            return rec
        o, h, l, c = bar
        rec["daily"] = {"date": "2026-09-09", "open": f(o), "high": f(h), "low": f(l), "close": f(c)}
        bl, bh = row["buy_low"], row["buy_high"]
        prot = row["protect"]
        s1l = row["sell1_low"]
        chase = row["chase_line"]

        # 标签判定
        in_buy_zone = (l <= bh) and (h >= bl)
        broke_protect = l < prot
        hit_sell1 = h >= s1l  # 达卖一（标签口径）：当日高点 ≥ 卖一下沿
        gap_pct = (o / bh - 1) * 100
        if in_buy_zone and broke_protect:
            label = "失败"
        elif in_buy_zone and (not broke_protect) and (hit_sell1 or c >= bh):
            label = "成功"
        elif in_buy_zone:
            # 入区/未破保护/未达卖一/收盘<入场价：标签口径缺口，按最保守路径计失败（9/2、9/3、9/7 先例）
            label = "失败(保守)"
        elif l > bh:
            label = "买不到" if o > bh else "未触发"
        else:
            label = "未触发"

        # A 口径（影子对照，V4）：buy_high 限价介入；出场保守优先级 保护位 > 卖一 > 收盘价。
        # 达卖一=当日高点≥卖一下沿（与标签口径一致；负 RR 合同卖一低于买区时必触发，
        # 属价格合同自身结构，按原值引用并记录待裁决）。
        a_ret = None; a_entry = None; a_exit = None; a_note = ""
        if in_buy_zone:
            a_entry = bh
            if broke_protect:
                a_exit, a_ret = prot, (prot / bh - 1) * 100
                a_note = "破保护位离场"
            elif hit_sell1:
                a_exit, a_ret = s1l, (s1l / bh - 1) * 100
                a_note = "达卖一"
            else:
                a_exit, a_ret = c, (c / bh - 1) * 100
                a_note = "收盘价"
        # 跳空买不到（open>buy_high 且 low>buy_high）：A 口径放弃不计数，但记录盘中触及
        # 卖一的事实（供报告点名，不产生模拟收益）。
        elif l > bh and o > bh:
            if hit_sell1:
                a_note = "买不到(盘中曾触卖一，不入场)"

        # V5 口径（现行）：跳空追买 + 近止盈
        v5_ret = None; v5_entry = None; v5_exit = None; v5_note = ""
        if o > bh and gap_pct <= 1.0 and o <= chase:
            v5_entry = o
            v5_note = "跳空追买(开盘)"
        elif in_buy_zone:
            v5_entry = bh
            v5_note = "买区限价"
        if v5_entry is not None:
            tgt = min(s1l, v5_entry * 1.03)
            # "触及"=盘中实际到达该价位（l<=tgt<=h）。当 tgt 低于入场价（负RR合同）时，
            # h>=tgt 会造成开盘即触发的荒谬读法，故统一用实际触及判定。
            touched = (l <= tgt <= h)
            if l < prot:
                v5_exit, v5_ret = prot, (prot / v5_entry - 1) * 100
                v5_note += "→破保护离场"
            elif touched:
                v5_exit, v5_ret = tgt, (tgt / v5_entry - 1) * 100
                v5_note += "→近止盈/卖一"
            else:
                v5_exit, v5_ret = c, (c / v5_entry - 1) * 100
                v5_note += "→收盘"

        rec.update({
            "label": label,
            "gap_pct_vs_buyhigh": f(gap_pct, 2),
            "A_entry": f(a_entry) if a_entry else None, "A_exit": f(a_exit) if a_exit else None,
            "A_ret": f(a_ret) if a_ret is not None else None, "A_note": a_note,
            "V5_entry": f(v5_entry) if v5_entry else None, "V5_exit": f(v5_exit) if v5_exit else None,
            "V5_ret": f(v5_ret) if v5_ret is not None else None, "V5_note": v5_note,
        })
        return rec

    for r in top5:
        out["top5"].append(judge(r))
    for r in sorted(formal, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"]))[5:]:
        out["formal_rest"].append(judge(r))
    for r in sorted(watch, key=lambda r: (-r["opportunity_score"], -r["rr_ratio"])):
        out["watch"].append(judge(r))

    # TOP1 基准：昨日 TOP1 今日开盘买入收盘卖出
    t1 = out["top5"][0] if out["top5"] else None
    if t1 is not None:
        code = t1["code"]
        bar = SNAP_TODAY.get(code)
        if bar:
            o, h, l, c = bar
            out["top1"] = {"code": code, "name": t1["name"], "score": t1["score"],
                           "open": f(o), "close": f(c),
                           "ret": f((c / o - 1) * 100, 2)}

    # 汇总统计
    def summarize(recs):
        cnt = {}
        a_rets, v5_rets = [], []
        for x in recs:
            cnt[x["label"]] = cnt.get(x["label"], 0) + 1
            if x.get("A_ret") is not None:
                a_rets.append(x["A_ret"])
            if x.get("V5_ret") is not None:
                v5_rets.append(x["V5_ret"])
        # 成功标签口径均值（历史"正式模拟收益%"列口径）
        succ_a = [x["A_ret"] for x in recs if x["label"] == "成功" and x.get("A_ret") is not None]
        succ_v5 = [x["V5_ret"] for x in recs if x["label"] == "成功" and x.get("V5_ret") is not None]
        return {"counts": cnt,
                "A_mean_all": f(statistics.mean(a_rets), 2) if a_rets else None,
                "V5_mean_all": f(statistics.mean(v5_rets), 2) if v5_rets else None,
                "A_mean_success": f(statistics.mean(succ_a), 2) if succ_a else "无成功",
                "V5_mean_success": f(statistics.mean(succ_v5), 2) if succ_v5 else "无成功",
                "A_n": len(a_rets), "V5_n": len(v5_rets)}

    out["sum_top5"] = summarize(out["top5"])
    out["sum_formal_rest"] = summarize(out["formal_rest"])
    out["sum_watch"] = summarize(out["watch"])
    all_formal = out["top5"] + out["formal_rest"]
    out["sum_formal_all"] = summarize(all_formal)

    print(json.dumps(out, ensure_ascii=False, indent=1))
    # 同步写一份 UTF-8 文件，避免控制台代码页乱码
    with open(os.path.join(BASE, "verdict_calc_20260909_out.json"), "w", encoding="utf-8") as jf:
        json.dump(out, jf, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

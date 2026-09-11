# -*- coding: utf-8 -*-
"""2026-09-11 收盘终判轮辅助计算（只读：读昨日扫描CSV×今日快照CSV，输出双口径模拟收益；不修改任何策略/代码）"""
import csv, statistics, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SCAN = r"D:\Environment\marketbase\data\cache\fast\scan_result_20260910_1531.csv"
SNAP = r"D:\Environment\marketbase\data\cache\fast\snapshot_latest.csv"

def fnum(x):
    x = (x or '').strip()
    return float(x) if x not in ('', '-') else None

# 昨日候选
cands = []
with open(SCAN, newline='', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        cands.append(r)

# 今日行情
today = {}
with open(SNAP, newline='', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        today[r['code']] = r

# 全市场中位数（所有有效 change_pct）
chgs = [fnum(r['change_pct']) for r in today.values() if fnum(r['change_pct']) is not None]
med = statistics.median(chgs)
up_ratio = sum(1 for c in chgs if c > 0) / len(chgs)
print(f"全市场 n={len(chgs)} 中位数={med:.3f}% 上涨占比={up_ratio*100:.1f}%")

def judge(c, t):
    code = c['code']; name = c['name']
    score = int(float(c['opportunity_score']))
    bl, bh = fnum(c['buy_low']), fnum(c['buy_high'])
    cl, pr = fnum(c['chase_line']), fnum(c['protect'])
    s1l, s1h = fnum(c['sell1_low']), fnum(c['sell1_high'])
    o, h, l, cl_p = float(t['open']), float(t['high']), float(t['low']), float(t['price'])
    tr = int(c['tail_risk_count'])

    # ---- 标签 ----
    in_zone = (l <= bh) and (h >= bl)
    broke = l < pr
    if open_ := None: pass
    label = None
    if in_zone and not broke and (h >= s1l or cl_p >= bl):
        label = '成功'
    elif in_zone and broke:
        label = '失败'
    elif o > bh and l > bh:
        label = '买不到'
    elif l > bh:
        label = '未触发'
    else:
        # 入区（触及buy_low~buy_high）但未破保护、未达卖一且收盘<入场（入场取 buy_low 最近点）
        label = '失败' if cl_p < bl else '未触发'
    # 修正: 成功条件中"收盘价>=入场价"的入场应指 buy_high（限价介入价）
    if in_zone and not broke and not (h >= s1l or cl_p >= bh):
        label = '失败' if cl_p < bh else '未触发'

    # ---- A口径 ----
    a_ret = None; a_desc = ''
    if in_zone:
        if broke:
            a_desc = f'入区破保护 {bh:.2f}->{pr:.2f}'
            a_ret = (pr / bh - 1) * 100
        elif h >= s1l:
            a_desc = f'入区达卖一 {bh:.2f}->{s1l:.2f}'
            a_ret = (s1l / bh - 1) * 100
        else:
            a_desc = f'入区收盘出 {bh:.2f}->{cl_p:.2f}'
            a_ret = (cl_p / bh - 1) * 100
    elif o > bh and l > bh:
        a_desc = '买不到(不计入)'
    else:
        a_desc = '未触发(不计入)'

    # ---- V5口径 ----
    v5_ret = None; v5_desc = ''
    if o <= bh:
        entry = o
        tp = min(s1l, entry * 1.03)
        if l < pr:
            v5_desc = f'预挂成交 {entry:.2f} 破保护出 {pr:.2f}'
            v5_ret = (pr / entry - 1) * 100
        elif h >= tp:
            v5_desc = f'预挂成交 {entry:.2f} 近止盈出 {tp:.2f}'
            v5_ret = (tp / entry - 1) * 100
        else:
            v5_desc = f'预挂成交 {entry:.2f} 收盘出 {cl_p:.2f}'
            v5_ret = (cl_p / entry - 1) * 100
    elif o > bh and o <= bh * 1.01 and o <= cl:
        entry = o
        tp = min(s1l, entry * 1.03)
        if l < pr:
            v5_desc = f'追买 {entry:.2f} 破保护出 {pr:.2f}'
            v5_ret = (pr / entry - 1) * 100
        elif h >= tp:
            v5_desc = f'追买 {entry:.2f} 近止盈出 {tp:.2f}'
            v5_ret = (tp / entry - 1) 	* 100
        else:
            v5_desc = f'追买 {entry:.2f}连收盘出 {cl_p:.2f}'
            v5_ret = (cl_p / entry - 1) * 100
    elif l <= bh:
        entry = bh
        tp = min(s1l, entry * 1.03)
        if l < pr:
            v5_desc = f'回踩接回 {entry:.2f} 研保护出 {pr:.2f}'
            v5_ret = (pr / entry - 1) * 100
        elif h >= tp:
            v5_desc = f'回踩接回 {entry:.2f} 近止盈出 {tp:.2f}'
            v5_ret = (tp / entry - 1) * 100
        else:
            v5_desc = f'回踩接回 {entry:.2f} 收盘出 {cl_p:.2f}'
            v5_ret = (cl_p / entry - 1) * 100
    else:
        v5_desc = '放弃(跳空>1%未回踩,不计入)'

    return dict(code=code, name=name, score=score, tr=tr, label=label,
                bl=bl, bh=bh, cl=cl, pr=pr, s1l=s1l, s1h=s1h,
                o=o, h=h, l=l, c=cl_p,
                a_ret=a_ret, a_desc=a_desc, v5_ret=v5_ret, v5_desc=v5_desc)

rows = [judge(c, today[c['code']]) for c in cands if c['code'] in today]
formal = [r for r in rows if r['tr'] == 0]
watch = [r for r in rows if r['tr'] > 0]
formal_sorted = sorted(formal, key=lambda r: (-r['score'], r['name']))
watch_sorted = sorted(watch, key=lambda r: (-r['score'], r['name']))

print("\n=== 正式名单（tail_risk_count==0）===")
for r in formal_sorted:
    a = f"{r['a_ret']:+.2f}%" if r['a_ret'] is not None else '不计数'
    v = f"{r['v5_ret']:+.2f}%" if r['v5_ret'] is not None else '不计数'
    print(f"{r['code']} {r['name']} {r['score']}分 O{r['o']:.2f}/H{r['h']:.2f}/L{r['l']:.2f}/C{r['c']:.2f} | 买区{r['bl']:.2f}-{r['bh']:.2f} 卖一{r['s1l']:.2f}-{r['s1h']:.2f} 保护{r['pr']:.2f} 禁追{r['cl']:.2f} | {r['label']} | A:{a} ({r['a_desc']}) | V5:{v} ({r['v5_desc']})")

print("\n=== 条件观察名单 ===")
for r in watch_sorted:
    a = f"{r['a_ret']:+.2f}%" if r['a_ret'] is not None else '不计数'
    v = f"{r['v5_ret']:+.2f}%" if r['v5_ret'] is not None else '不计数'
    print(f"{r['code']} {r['name']} {r['score']}分 TR{r['tr']} O{r['o']:.2f}/H{r['h']:.2f}/L{r['l']:.2f}/C{r['c']:.2f} | 买区{r['bl']:.2f}-{r['bh']:.2f} 卖一{r['s1l']:.2f}-{r['s1h']:.2f} 保护{r['pr']:.2f} | {r['label']} | A:{a} | V5:{v}")

def stats(rs):
    lab = {}
    for r in rs: lab[r['label']] = lab.get(r['label'], 0) + 1
    a_list = [r['a_ret'] for r in rs if r['a_ret'] is not None]
    v_list = [r['v5_ret'] for r in rs if r['v5_ret'] is not None]
    return lab, (statistics.mean(a_list) if a_list else None), (statistics.mean(v_list) if v_list else None)

for nm, rs in [('正式', formal_sorted), ('观察', watch_sorted), ('TOP5', formal_sorted[:5])]:
    lab, am, vm = stats(rs)
    a = f"{am:+.2f}%" if am is not None else '无'
    v = f"{vm:+.2f}%" if vm is not None else '无'
    print(f"\n{nm}: 标签{lab} A均值={a} V5均值={v}")

# TOP1 基准
if formal_sorted:
    t1 = formal_sorted[0]
    print(f"\nTOP1 基准: {t1['name']} {t1['code']} 开盘{t1['o']:.2f}买入 收盘{t1['c']:.2f}卖出 = {(t1['c']/t1['o']-1)*100:+.2f}%")

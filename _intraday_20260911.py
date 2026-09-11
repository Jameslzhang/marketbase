# -*- coding: utf-8 -*-
"""提取今日各时段扫描中 8 只终判对象的日内高低点演进（只读）"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
codes = ['603175','603115','603268','601336','002916','002913','603228','001389']
for ts in ['1132','1402','1502','1544']:
    try:
        d = json.load(open(rf'D:\Environment\marketbase\data\cache\fast\candidate_union_20260911_{ts}.json', encoding='utf-8'))
        cands = d['candidates']
        obs = d['observed_at']
        print(f"=== {ts} observed_at={obs} n_candidates={len(cands)} ===")
        for item in cands:
            if item['code'] in codes:
                print(f"  {item['code']} {item['name']} price={item['price']} open={item['open']} high={item['high']} low={item['low']}")
    except FileNotFoundError:
        print(f"=== {ts} 不存在 ===")

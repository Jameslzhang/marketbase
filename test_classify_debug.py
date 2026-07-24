"""Test: per-industry classification via xuangu API.
Uses _eastmoney_get for ALL requests (push2 + xuangu) to respect rate limits."""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from marketbase.snapshot import _eastmoney_get

XUANGU_URL = 'https://data.eastmoney.com/dataapi/xuangu/list'
PUSH2_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/xuangu/'}

# Step 1: Get industry list
print("Step 1: Getting industry names...")
industries = []
try:
    r = _eastmoney_get(PUSH2_URL, params={
        'pn': '1', 'pz': '200', 'po': '1', 'np': '1',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:90+t:2', 'fields': 'f12,f14',
    }, headers=HEADERS, timeout=30)
    d = r.json()
    if d.get('data') and d['data'].get('diff'):
        industries = [it['f14'] for it in d['data']['diff']]
        print(f"Got {len(industries)} industries from push2")
        for name in industries[:5]:
            print(f"  {name}")
except Exception as e:
    print(f"Push2 failed: {e}")

if not industries:
    print("Falling back to xuangu industry list...")
    r = _eastmoney_get(XUANGU_URL, params={
        "st": "SECURITY_CODE", "sr": "1", "ps": "500", "p": "1",
        "sty": "INDUSTRY",
        "filter": '(MARKET+in+("上交所主板","深交所主板","深交所创业板","上交所科创板","北交所"))',
        "source": "SELECT_SECURITIES", "client": "WEB",
    }, headers=HEADERS, timeout=30)
    data = r.json()
    industry_set = set()
    for item in data["result"]["data"]:
        ind = str(item.get("INDUSTRY", "")).strip()
        if ind:
            industry_set.add(ind)
    industries = sorted(industry_set)
    print(f"Got {len(industries)} industries from xuangu fallback")

# Step 2: Query each industry
all_rows = {}
print(f"\nStep 2: Querying {len(industries)} industries...")
for i, industry in enumerate(industries):
    if i > 0:
        time.sleep(1.0)
    print(f"[{i+1}/{len(industries)}] {industry}...", end=" ", flush=True)
    try:
        r = _eastmoney_get(XUANGU_URL, params={
            "st": "SECURITY_CODE", "sr": "1", "ps": "500", "p": "1",
            "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,INDUSTRY,CONCEPT,BOARD_NAME",
            "filter": f'(INDUSTRY+in+("{industry}"))',
            "source": "SELECT_SECURITIES", "client": "WEB",
        }, headers=HEADERS, timeout=30)
        d = r.json()
        items = d["result"]["data"]
        new_count = 0
        for item in items:
            code = str(item.get("SECURITY_CODE", "")).strip()
            if code and len(code) == 6 and code not in all_rows:
                name = str(item.get("SECURITY_NAME_ABBR", "")).strip()
                ind = str(item.get("INDUSTRY", "")).strip() or str(item.get("BOARD_NAME", "")).strip()
                concepts_raw = item.get("CONCEPT", [])
                if isinstance(concepts_raw, list):
                    concepts = ", ".join(str(c).strip() for c in concepts_raw if str(c).strip())
                else:
                    concepts = str(concepts_raw).strip() if concepts_raw else ""
                all_rows[code] = {"code": code, "name": name, "industry": ind, "concepts": concepts}
                new_count += 1
        print(f"{len(items)} items, {new_count} new (total: {len(all_rows)})")
    except Exception as e:
        print(f"ERROR: {e}")

print(f"\nDone! Total unique stocks: {len(all_rows)}")
print(f"Unique industries: {len(set(r['industry'] for r in all_rows.values()))}")

# Save to CSV
import pandas as pd
from datetime import datetime, timezone
now_str = datetime.now(timezone.utc).isoformat()
rows = []
for code, r in sorted(all_rows.items()):
    rows.append({**r, "supply_chain": "", "source": "em_datacenter", "updated_at": now_str})
df = pd.DataFrame(rows, columns=["code","name","industry","concepts","supply_chain","source","updated_at"])
out = "data/daily_runs/classification_source.csv"
df.to_csv(out, index=False, encoding="utf-8")
print(f"Saved to {out}")
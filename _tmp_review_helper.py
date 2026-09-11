# -*- coding: utf-8 -*-
"""复盘轮辅助取数：全市场中位数 + BYD 轮动信号核查（临时脚本，只读取数）。"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd  # noqa: E402

from marketbase.snapshot import fetch_cn_snapshot  # noqa: E402

snap = None
for src in ["sina", "efinance", "akshare_em"]:
    for attempt in range(2):
        try:
            df = fetch_cn_snapshot(src)
            if not df.empty:
                snap = df
                print(f"source={src} rows={len(df)}")
                break
        except Exception as e:
            print(f"{src} attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(3)
    if snap is not None:
        break
if snap is None:
    print("ALL_SOURCES_FAILED")
    sys.exit(1)

snap["code"] = snap["code"].astype(str).str.strip().str.zfill(6)
chg = pd.to_numeric(snap["change_pct"], errors="coerce")
med = chg.median()
up_ratio = (chg > 0).mean()
if "observed_at" in snap.columns:
    print(f"observed_at={snap['observed_at'].iloc[0]}")
else:
    print("observed_at=n/a")
print(f"median_change_pct={med:+.2f}%")
print(f"up_ratio={up_ratio*100:.1f}%")

byd = snap[snap["code"] == "002594"][["code", "name", "price", "pre_close",
                                      "open", "high", "low", "change_pct"]]
print(byd.to_string(index=False))

# 开盘口径中位数（若快照含 open/pre_close）
if "open" in snap.columns and "pre_close" in snap.columns:
    open_ret = (pd.to_numeric(snap["open"], errors="coerce")
                / pd.to_numeric(snap["pre_close"], errors="coerce") - 1)
    valid = open_ret.dropna()
    print(f"open_median={valid.median()*100:+.2f}%")
    print(f"open_up_ratio={(valid > 0).mean()*100:.1f}%")
else:
    print("open_median=NO_OPEN_COLUMN")

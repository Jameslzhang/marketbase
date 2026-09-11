# -*- coding: utf-8 -*-
"""诊断：逐个测试快照源的网络连通性（只读探测，不改任何数据）。"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import requests

proxies = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}

def test(name, fn):
    try:
        r = fn()
        print(f"[OK]   {name}: {r}")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")

# 1. 新浪行情简单接口（hq.sinajs.cn，PowerShell 已验证可达）
def t1():
    r = requests.get("https://hq.sinajs.cn/list=sh603019",
                     headers={"Referer": "https://finance.sina.com.cn"}, timeout=10)
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:40]!r}"
test("sina hq.sinajs.cn (no proxy)", lambda: t1())

# 1b. 同一接口走本地代理 7890
def t1p():
    r = requests.get("https://hq.sinajs.cn/list=sh603019",
                     headers={"Referer": "https://finance.sina.com.cn"}, timeout=10, proxies=proxies)
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:40]!r}"
test("sina hq.sinajs.cn (proxy 7890)", t1p)

# 2. 新浪市场中心接口（_fetch_sina 用的 vip.stock.finance.sina.com.cn）
def t2():
    s = requests.Session()
    r = s.get("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
              params={"page": 1, "num": 5, "sort": "symbol", "asc": 1, "node": "sh_a", "symbol": "", "_s_r_a": "page"},
              headers={"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/mkt/"},
              timeout=(10, 30))
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:60]!r}"
test("sina Market_Center (no proxy)", t2)

# 3. 东财 push2（efinance 用的）
def t3():
    r = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                     params={"pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12",
                             "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                             "fields": "f12,f14"},
                     timeout=10)
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:60]!r}"
test("eastmoney push2 (no proxy)", t3)

# 3b. 东财 push2 http（非 https，报错里用的是 http://）
def t3h():
    r = requests.get("http://push2.eastmoney.com/api/qt/clist/get",
                     params={"pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12",
                             "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                             "fields": "f12,f14"},
                     timeout=10)
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:60]!r}"
test("eastmoney push2 http (no proxy)", t3h)

# 3c. 东财 push2 + 浏览器 UA + Referer（判断是否 UA 指纹被拒）
def t3c():
    r = requests.get("https://push2.eastmoney.com/api/qt/clist/get",
                     params={"pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f12",
                             "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                             "fields": "f12,f14"},
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                              "Referer": "https://quote.eastmoney.com/"},
                     timeout=10)
    r.raise_for_status()
    return f"status={r.status_code} len={len(r.text)} head={r.text[:60]!r}"
test("eastmoney push2 + browser UA (no proxy)", t3c)

# 4. 检查环境代理变量
import os
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
    print(f"env {k}={os.environ.get(k)!r}")

# 5. requests 默认 UA 是什么
print("requests default UA:", requests.utils.default_user_agent())
print("requests version:", requests.__version__)


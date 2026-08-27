# -*- coding: utf-8 -*-
r"""
fast_t1_scan.py — 全盘 T+1 策略快速扫描（一键）

设计目标：绕开官方管道的审计/分类/分钟数据等重步骤，
只做扫描真正需要的四件事：快照 → 量比 → 指标 → 评分出报告。

提速要点：
  1. 快照新鲜度复用：同日快照若 < --fresh 分钟直接复用，否则实时采集（约 30 秒）
  2. 5日均量日级缓存：量比计算首次约 1 分钟（并行），同日重复扫描 <1 秒
  3. 指标并行计算：只对流动性过滤后的候选并行读取日线缓存并现算指标，
     且用快照最新价补丁当日 bar（收盘后运行即为收盘口径）
  4. 全程无网络依赖的审计/分类步骤，不会被挂起阻塞

用法：
    python fast_t1_scan.py                     # 默认：快照 15 分钟内复用，否则新采
    python fast_t1_scan.py --fresh 0           # 强制重新采集快照
    python fast_t1_scan.py --workers 32        # 并行线程数（默认 24）
    python fast_t1_scan.py --html D:\x\y.html  # 指定报告输出路径

输出：
    {项目根}\data\cache\fast\scan_result_{日期}_{时分}.csv   候选明细
    {项目根}\data\cache\avg5d_volume_{日期}.csv              5日均量缓存（同日共享）
    HTML 报告（默认写入 {项目根}\reports\，可用 --html 覆盖）

依赖：本脚本须放在 marketbase 项目根目录（与 local_workflow.py 同级），
通过 __file__ 自动定位项目根，无硬编码绝对路径，可随项目整体迁移。

策略口径与 full_market_scan_v2_intraday.py 完全一致：
    硬过滤 → 市场环境 → 流动性 → 趋势(MA5>MA10>MA20) → 双轴评分
    → 买卖区(含禁追线 0.5% 容差) → 行业同步过滤 → 正式候选/观察池
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

MB_ROOT = Path(__file__).resolve().parent  # 脚本位于 marketbase 项目根目录
if str(MB_ROOT) not in sys.path:
    sys.path.insert(0, str(MB_ROOT))

from marketbase.market_collector import collect_market_snapshot  # noqa: E402
from marketbase.indicators import compute_daily_indicators  # noqa: E402
from marketbase.volume_ratio import _avg_5d_volume, elapsed_trade_minutes  # noqa: E402

REPORT_DIR = MB_ROOT / "reports"  # HTML 报告默认输出目录（可用 --html 覆盖）

IND_FIELDS = ["ma5", "ma10", "ma20", "ma60", "rsi14", "atr14", "atr14_pct",
              "boll_upper", "boll_middle", "boll_lower", "boll_position",
              "return_5d", "return_10d", "return_20d",
              "upper_shadow_ratio", "lower_shadow_ratio", "input_rows"]

CN_TZ = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(f"[{datetime.now(CN_TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def official_daily_cache_root(data_root: Path) -> Path:
    """官方管道（local_workflow.py）使用的日线缓存：data/daily_runs/cache/daily。

    快速扫描必须复用该缓存，才能与官方 post_close 产物的指标完全一致；
    旧的 data/cache/daily 为早期遗留缓存，数据与官方不同，已弃用。
    """
    primary = data_root / "daily_runs" / "cache" / "daily"
    if primary.is_dir():
        return primary
    fallback = data_root / "cache" / "daily"
    if fallback.is_dir():
        log(f"警告：官方日线缓存 {primary} 不存在，回退到旧缓存 {fallback}（指标可能与官方不一致）")
        return fallback
    return primary


# ────────────────────────── 快照 ──────────────────────────

def get_snapshot(data_root: Path, observed_at: datetime, fresh_minutes: int) -> tuple[pd.DataFrame, str, float]:
    """返回 (快照 DataFrame, 来源说明, 耗时秒)。"""
    fast_dir = data_root / "cache" / "fast"
    fast_dir.mkdir(parents=True, exist_ok=True)
    snap_csv = fast_dir / "snapshot_latest.csv"

    if fresh_minutes > 0 and snap_csv.is_file():
        try:
            cached = pd.read_csv(snap_csv, dtype={"code": str})
            obs_raw = str(cached["observed_at"].iloc[0])
            obs = pd.to_datetime(obs_raw)
            if obs.tzinfo is None:
                obs = obs.tz_localize(CN_TZ)
            age_min = (observed_at - obs).total_seconds() / 60.0
            if obs.date() == observed_at.date() and 0 <= age_min <= fresh_minutes:
                return cached, f"复用缓存快照（{age_min:.0f} 分钟前）", 0.0
        except Exception:
            pass  # 缓存损坏则重新采集

    t0 = time.perf_counter()

    def progress(msg: str) -> None:
        log(f"  快照采集: {msg}")

    result = collect_market_snapshot(
        cache_path=data_root / "cache" / "market_snapshot.json",
        now=observed_at,
        progress=progress,
    )
    df = result.frame.copy()
    df["code"] = df["code"].astype(str).str.strip().str.zfill(6)
    df.to_csv(snap_csv, index=False, encoding="utf-8-sig")
    return df, "实时采集", time.perf_counter() - t0


# ────────────────────────── 行业字段回补 ──────────────────────────

def _industry_coverage(frame: pd.DataFrame) -> float:
    if "industry" not in frame.columns:
        return 0.0
    return float(frame["industry"].fillna("").astype(str).str.strip().ne("").mean())


def ensure_industry(df: pd.DataFrame, data_root: Path) -> tuple[pd.DataFrame, str]:
    """快照行业字段缺失时，从最近的 run 快照回补（行业归属极少变化）。"""
    if _industry_coverage(df) >= 0.5:
        return df, "快照自带"
    paths = sorted((data_root / "daily_runs").glob("*/*/market_snapshot.csv"), reverse=True)[:6]
    for path in paths:
        try:
            ref = pd.read_csv(path, dtype={"code": str})
        except Exception:
            continue
        if _industry_coverage(ref) < 0.5:
            continue
        ref["code"] = ref["code"].astype(str).str.zfill(6)
        mapping = ref.drop_duplicates("code").set_index("code")["industry"]
        df = df.copy()
        mapped = df["code"].map(mapping)
        if "industry" in df.columns:
            df["industry"] = mapped.fillna(df["industry"])
        else:
            df["industry"] = mapped
        return df, f"回补自 {path.parent.parent.name}/{path.parent.name}"
    return df, "缺失（无回补源）"


# ────────────────────────── 量比（5日均量缓存） ──────────────────────────

def _compute_avg5d(codes: list[str], daily_root: Path, observed_at: datetime,
                   workers: int) -> pd.Series:
    def one(code: str) -> tuple[str, float | None]:
        try:
            return code, _avg_5d_volume(code, daily_root, observed_at=observed_at)
        except Exception:
            return code, None

    results: list[tuple[str, float | None]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, item in enumerate(pool.map(one, codes, chunksize=64), 1):
            results.append(item)
            if i % 1000 == 0:
                log(f"  5日均量计算进度 {i}/{len(codes)}")
    return pd.Series([v for _, v in results], index=[c for c, _ in results], name="avg5d")


def get_avg5d_volume(data_root: Path, codes: list[str], observed_at: datetime,
                     workers: int) -> tuple[pd.Series, str, float]:
    """返回 (code→5日均量 Series, 来源说明, 耗时秒)。缓存按自然日隔离，缺失代码增量补算。"""
    cache_path = data_root / "cache" / f"avg5d_volume_v2_{observed_at.strftime('%Y%m%d')}.csv"
    daily_root = official_daily_cache_root(data_root)

    def _save(series: pd.Series) -> None:
        out = series.dropna().reset_index()
        out.columns = ["code", "avg5d"]
        out["code"] = out["code"].astype(str).str.zfill(6)
        out.to_csv(cache_path, index=False, encoding="utf-8-sig")

    if cache_path.is_file():
        t0 = time.perf_counter()
        cached = pd.read_csv(cache_path, dtype={"code": str})
        cached["code"] = cached["code"].astype(str).str.zfill(6)
        series = cached.set_index("code")["avg5d"]
        missing = [c for c in codes if c not in series.index]
        if missing:
            added = _compute_avg5d(missing, daily_root, observed_at, workers).dropna()
            if not added.empty:
                series = pd.concat([series, added])
                _save(series)
            return series, f"缓存命中（补算 {len(missing)} 只）", time.perf_counter() - t0
        return series, "缓存命中", time.perf_counter() - t0

    t0 = time.perf_counter()
    series = _compute_avg5d(codes, daily_root, observed_at, workers)
    _save(series)
    return series, f"并行计算（{workers} 线程）", time.perf_counter() - t0


def apply_volume_ratio(df: pd.DataFrame, avg5d: pd.Series, observed_at: datetime) -> pd.DataFrame:
    elapsed = elapsed_trade_minutes(observed_at)
    vol = pd.to_numeric(df["volume"], errors="coerce")
    avg = df["code"].map(avg5d)
    if elapsed >= 240:
        vr = vol / avg
    elif elapsed > 0:
        vr = vol / (avg * elapsed / 240.0)
    else:
        vr = pd.Series(np.nan, index=df.index)
    df = df.copy()
    df["volume_ratio"] = vr.round(4)
    df["elapsed_trade_minutes"] = elapsed
    return df


# ────────────────────────── 指标（线程池并行，口径与官方一致：排除当日 bar） ──────────────────────────

def _indicator_record(code: str, daily_root: Path, today_str: str) -> dict | None:
    fp = daily_root / f"{code}.json"
    if not fp.is_file():
        return None
    try:
        payload = json.loads(fp.read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or not rows:
            return None
        # 与官方管道口径完全一致：lookback=260 根、trading_date 排除当日 bar、内部 tail(250)
        frame = pd.DataFrame(rows[-260:])
        ind = compute_daily_indicators(frame, trading_date=today_str)
        record = {"code": code}
        record.update({k: ind.get(k) for k in IND_FIELDS})
        return record
    except Exception:
        return None


def compute_indicators_parallel(df: pd.DataFrame, daily_root: Path,
                                today_str: str, workers: int) -> pd.DataFrame:
    codes = df["code"].tolist()
    records: list[dict] = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(workers, 32)) as pool:
        for i, item in enumerate(pool.map(
                lambda c: _indicator_record(c, daily_root, today_str),
                codes, chunksize=32), 1):
            if item is not None:
                records.append(item)
            if i % 1000 == 0:
                log(f"  指标进度 {i}/{len(codes)}（{time.perf_counter() - t0:.0f}s）")
    return pd.DataFrame(records)


# ────────────────────────── 评分与买卖区（与 v2 口径一致） ──────────────────────────

def calc_opportunity_score(row, ind_chg: pd.Series):
    score = 0
    tags = []
    if row["trend_aligned"]:
        score += 20; tags.append("trend_full")
    elif row["trend_near"]:
        score += 10; tags.append("trend_partial")
    rsi = row["rsi14"]
    if 45 <= rsi <= 65:
        score += 15; tags.append(f"rsi_healthy({rsi:.0f})")
    elif 40 <= rsi <= 70:
        score += 8; tags.append(f"rsi_ok({rsi:.0f})")
    vr = row.get("volume_ratio", np.nan)
    if pd.notna(vr):
        if 1.2 <= vr <= 3.0:
            score += 15; tags.append(f"vr_good({vr:.1f})")
        elif 0.8 <= vr < 1.2:
            score += 5; tags.append(f"vr_normal({vr:.1f})")
    chg = row["change_pct"]
    if 0.5 <= chg <= 5.0:
        score += 10; tags.append(f"chg_moderate({chg:+.1f}%)")
    elif -1.0 <= chg < 0.5:
        score += 5; tags.append(f"chg_flat({chg:+.1f}%)")
    if pd.notna(row.get("amount")) and pd.notna(row.get("volume")) and row["volume"] > 0:
        vwap = row["amount"] / row["volume"]
        if row["price"] > vwap:
            score += 10; tags.append("above_vwap")
    ind = row.get("industry", "")
    ind_v = ind_chg.get(ind, np.nan) if pd.notna(ind) and ind != "" else np.nan
    if pd.notna(ind_v) and ind_v > 0:
        score += 10; tags.append(f"ind_sync({ind_v:+.1f}%)")
    elif pd.notna(ind_v):
        tags.append(f"ind_weak({ind_v:+.1f}%)")
    ret20 = row.get("return_20d", np.nan)
    if pd.notna(ret20) and ret20 > 0:
        score += 10; tags.append(f"ret20_pos({ret20*100:+.1f}%)")
    elif pd.notna(ret20) and ret20 > -0.05:
        score += 5; tags.append("ret20_flat")
    return score, tags


def calc_tail_risk(row):
    tags = []
    atr_pct = row.get("atr14_pct", np.nan)
    if pd.notna(atr_pct) and atr_pct > 6.0:
        tags.append("high_atr")
    boll = row.get("boll_position", np.nan)
    if pd.notna(boll) and boll > 0.95:
        tags.append("boll_overbought")
    tor = row.get("turnover_rate", np.nan)
    if pd.notna(tor) and tor > 10.0:
        tags.append("high_turnover")
    ushadow = row.get("upper_shadow_ratio", np.nan)
    if pd.notna(ushadow) and ushadow > 0.5:
        tags.append("long_upper_shadow")
    return tags


def calc_zones(row) -> pd.Series:
    price = row["price"]
    atr = row.get("atr14", price * 0.02)
    if pd.isna(atr) or atr <= 0:
        atr = price * 0.02
    boll_lower = row.get("boll_lower", price * 0.95)
    boll_upper = row.get("boll_upper", price * 1.05)
    if pd.isna(boll_lower):
        boll_lower = price * 0.95
    if pd.isna(boll_upper):
        boll_upper = price * 1.05
    buy_low = round(max(price - atr * 0.5, boll_lower), 2)
    buy_high = round(price - atr * 0.1, 2)
    chase_line = round((buy_high + atr * 0.3) * 1.005, 2)  # 禁追线含 0.5% 容差
    protect = round(buy_low - atr * 0.5, 2)
    sell1_low = round(boll_upper * 0.99, 2)
    sell1_high = round(boll_upper, 2)
    sell2_low = round(sell1_high + atr * 0.3, 2)
    sell2_high = round(sell1_high + atr * 0.8, 2)
    reward = sell1_low - buy_high
    risk = buy_high - protect
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    return pd.Series({
        "buy_low": buy_low, "buy_high": buy_high,
        "chase_line": chase_line, "protect": protect,
        "sell1_low": sell1_low, "sell1_high": sell1_high,
        "sell2_low": sell2_low, "sell2_high": sell2_high,
        "rr_ratio": rr, "atr": round(atr, 3),
    })


# ────────────────────────── HTML 报告 ──────────────────────────

def render_html(ctx: dict) -> str:
    def row_cells(r, detail: bool = False) -> str:
        tags = " ".join(
            f'<span class="tag">{t}</span>' for t in (r["opportunity_tags"][:5] if detail else [])
        )
        risk = ",".join(r["tail_risk_tags"]) if r["tail_risk_tags"] else "—"
        fast = ' <span class="tag fast">快速介入</span>' if r.get("fast_entry") else ""
        extra = f"<td class='small'>{tags}</td>" if detail else f"<td>{risk}</td>"
        return (
            f"<tr><td>{r['code']}</td><td>{r['name']}</td>"
            f"<td>{r['price']:.2f}</td><td>{r['change_pct']:+.2f}%</td>"
            f"<td><b>{r['opportunity_score']:.0f}</b>{fast}</td>"
            f"<td>{r['rr_ratio']:.2f}</td>"
            f"<td>{r['buy_low']:.2f}–{r['buy_high']:.2f}</td>"
            f"<td>{r['protect']:.2f}</td><td>{r['chase_line']:.2f}</td>"
            f"<td>{r['sell1_low']:.2f}–{r['sell1_high']:.2f}</td>"
            f"<td>{r.get('industry', '')}（{r['industry_chg']:+.1f}%）</td>{extra}</tr>"
        )

    top5_rows = "".join(row_cells(r, detail=True) for r in ctx["top5"])
    official_rows = "".join(row_cells(r) for r in ctx["official"])
    watch_rows = "".join(
        f"<tr><td>{r['code']}</td><td>{r['name']}</td><td>{r['price']:.2f}</td>"
        f"<td>{r['change_pct']:+.2f}%</td><td>{r['opportunity_score']:.0f}</td>"
        f"<td>{','.join(r['tail_risk_tags']) or '—'}</td><td>{r['rr_ratio']:.2f}</td>"
        f"<td>{r.get('industry', '')}</td></tr>"
        for r in ctx["watch"]
    )
    ind_top = "".join(f"<li>{name} <b>{v:+.2f}%</b></li>" for name, v in ctx["ind_top"])
    ind_bottom = "".join(f"<li>{name} <b>{v:+.2f}%</b></li>" for name, v in ctx["ind_bottom"])
    byd_cls = "bad" if ctx["byd"]["triggered"] else "ok"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>全盘T1策略扫描报告 {ctx['date']} {ctx['phase']}</title>
<style>
body{{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:#f6f7f9;color:#222;margin:0;padding:24px}}
.wrap{{max-width:1200px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 10px;border-left:4px solid #2b6cb0;padding-left:8px}}
.meta{{color:#666;font-size:13px;margin-bottom:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:#fff;border-radius:10px;padding:14px 18px;flex:1;min-width:220px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.card .k{{font-size:12px;color:#888}} .card .v{{font-size:20px;font-weight:700;margin-top:4px}}
.ok{{color:#1a7f37}} .bad{{color:#c62828}} .warn{{color:#b26a00}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);font-size:13px}}
th,td{{padding:7px 8px;text-align:left;border-bottom:1px solid #eef0f2;white-space:nowrap}}
th{{background:#2b6cb0;color:#fff;font-weight:600}}
tr:hover{{background:#f2f7fc}}
.tag{{display:inline-block;background:#e8f0fe;color:#2b6cb0;border-radius:4px;padding:1px 6px;font-size:11px;margin:1px 2px 1px 0}}
.tag.fast{{background:#c62828;color:#fff}}
.small{{font-size:11px;color:#555}}
ul{{margin:6px 0;padding-left:18px;font-size:13px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.note{{background:#fff8e6;border:1px solid #f0dca0;border-radius:8px;padding:10px 14px;font-size:13px;margin-top:8px}}
footer{{color:#999;font-size:12px;margin-top:28px}}
</style></head><body><div class="wrap">
<h1>全盘 T+1 策略扫描报告 · {ctx['date']} · {ctx['phase']}</h1>
<div class="meta">生成时间 {ctx['generated_at']} ｜ 快照 {ctx['snap_source']}（{ctx['snap_obs']}）｜ 全流程耗时 <b>{ctx['total_sec']:.0f} 秒</b>
（快照 {ctx['t_snap']:.0f}s / 量比 {ctx['t_vr']:.0f}s / 指标 {ctx['t_ind']:.0f}s）｜ 快速扫描模式（无审计网络步骤）</div>

<h2>市场环境</h2>
<div class="cards">
<div class="card"><div class="k">上涨占比</div><div class="v">{ctx['advance_ratio']:.1%}</div></div>
<div class="card"><div class="k">涨跌幅中位数</div><div class="v">{ctx['median']:+.2f}%</div></div>
<div class="card"><div class="k">市场模式</div><div class="v">{ctx['mode']}</div></div>
<div class="card"><div class="k">漏斗</div><div class="v" style="font-size:14px">{ctx['funnel']}</div></div>
</div>
<div class="grid2" style="margin-top:12px">
<div class="card"><div class="k">领涨行业</div><ul>{ind_top}</ul></div>
<div class="card"><div class="k">领跌行业</div><ul>{ind_bottom}</ul></div>
</div>

<h2>BYD-半导体轮动信号</h2>
<div class="cards"><div class="card" style="flex:2">
<div class="k">比亚迪（002594）今日涨跌</div>
<div class="v {byd_cls}">{ctx['byd']['chg']:+.2f}%（{ctx['byd']['price']:.2f} 元）</div>
<div style="font-size:13px;margin-top:6px">{ctx['byd']['text']}</div>
</div></div>

<h2>TOP 5 精选（含买卖区）</h2>
<table><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th><th>评分</th><th>RR</th><th>买区</th><th>保护位</th><th>禁追线</th><th>卖一</th><th>行业</th><th>标签</th></tr>
{top5_rows}</table>

<h2>正式候选（{ctx['n_official']} 只）</h2>
<table><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th><th>评分</th><th>RR</th><th>买区</th><th>保护位</th><th>禁追线</th><th>卖一</th><th>行业</th><th>尾部风险</th></tr>
{official_rows}</table>

<h2>条件观察（{ctx['n_watch']} 只）</h2>
<table><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌</th><th>评分</th><th>风险项</th><th>RR</th><th>行业</th></tr>
{watch_rows}</table>

<h2>执行纪律</h2>
<div class="note">
① 仅在价格回踩<b>买区</b>时挂限价单介入，<b>禁追线</b>为硬边界，突破不追；
② 评分 ≥85 标记"快速介入"的标的可不等深度回踩（仍不超过禁追线）；
③ 尾盘 14:50 仍未进入买区则放弃当日该标的；
④ 单只仓位 ≤8%，组合合计 ≤30%；跌破保护位无条件离场（T+1 次日执行）；
⑤ 本报告为量化筛选结果，不构成投资建议。
</div>
<footer>MarketBase 快速扫描 · fast_t1_scan.py · {ctx['generated_at']}</footer>
</div></body></html>"""


# ────────────────────────── 主流程 ──────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="全盘 T+1 策略快速扫描")
    parser.add_argument("--data-root", type=Path, default=MB_ROOT / "data")
    parser.add_argument("--fresh", type=int, default=15, help="快照复用新鲜度（分钟），0=强制新采")
    parser.add_argument("--workers", type=int, default=24, help="并行线程数")
    parser.add_argument("--html", type=Path, default=None, help="HTML 报告输出路径")
    args = parser.parse_args()

    t_start = time.perf_counter()
    observed_at = datetime.now(CN_TZ)
    today_str = observed_at.date().isoformat()
    if observed_at.hour >= 15:
        phase = "收盘"
    elif (observed_at.hour == 11 and observed_at.minute >= 30) or observed_at.hour == 12:
        phase = "午间"
    else:
        phase = "盘中"
    log(f"快速扫描启动 | {today_str} | {phase} | 数据根 {args.data_root}")

    # ① 快照
    df, snap_source, t_snap = get_snapshot(args.data_root, observed_at, args.fresh)
    snap_obs = str(df["observed_at"].iloc[0])[:19]
    log(f"① 快照就绪：{len(df)} 只 | {snap_source} | {t_snap:.1f}s")
    df, ind_source = ensure_industry(df, args.data_root)
    log(f"   行业字段：{ind_source}")

    # BYD 信号（基于完整快照，先取再过滤）
    byd_hit = df[df["code"].astype(str).str.zfill(6) == "002594"]
    byd_info = {"chg": float("nan"), "price": float("nan"), "triggered": False, "text": ""}
    if not byd_hit.empty:
        b = byd_hit.iloc[0]
        byd_info["chg"] = float(pd.to_numeric(b["change_pct"], errors="coerce"))
        byd_info["price"] = float(pd.to_numeric(b["price"], errors="coerce"))
        byd_info["triggered"] = byd_info["chg"] <= -1.0
        if byd_info["triggered"]:
            byd_info["text"] = "收盘跌幅 ≤ -1%，信号触发：次日半导体板块历史胜率 73.3%（33/45），可关注半导体轮动机会。"
        else:
            byd_info["text"] = f"跌幅未达 -1%（{byd_info['chg']:+.2f}%），信号未触发：次日半导体无轮动信号加持，相关持仓按既定纪律执行。"
    else:
        byd_info["text"] = "快照中未找到比亚迪行情，信号状态未知。"

    # ② 硬过滤
    initial = len(df)
    for col in ("is_st", "is_suspended", "delist_risk"):
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].fillna(False).astype(bool)
    df = df[df["is_st"] != True]          # noqa: E712
    df = df[df["is_suspended"] != True]   # noqa: E712
    df = df[df["delist_risk"] != True]    # noqa: E712
    # 名称法剔除 ST/退市风险（部分数据源 is_st 标记可能缺失）
    name = df["name"].fillna("").astype(str)
    df = df[~name.str.contains("ST", case=False) & ~name.str.contains("退")]
    chg_abs = pd.to_numeric(df["change_pct"], errors="coerce").fillna(0)
    df = df[(chg_abs < 9.9) & (chg_abs > -9.9)]  # 涨跌停近似剔除
    df = df[df["market"] != "bj"]
    df = df[pd.to_numeric(df["volume"], errors="coerce").fillna(0) > 0]
    if "listed_days" in df.columns:
        ld = pd.to_numeric(df["listed_days"], errors="coerce")
        df = df[(ld.isna()) | (ld >= 60)]
    df["industry"] = df["industry"].fillna("未知") if "industry" in df.columns else "未知"
    after_hard = len(df)
    log(f"② 硬过滤：{initial} -> {after_hard}")

    # ②.5 量比（5日均量缓存 + 向量化；在流动性过滤前对全集计算，同日缓存覆盖更广）
    t0 = time.perf_counter()
    avg5d, vr_source, _ = get_avg5d_volume(args.data_root, df["code"].tolist(), observed_at, args.workers)
    df = apply_volume_ratio(df, avg5d, observed_at)
    t_vr = time.perf_counter() - t0
    vr_cov = int(df["volume_ratio"].notna().sum())
    log(f"②.5 量比完成：覆盖 {vr_cov}/{len(df)} | {vr_source} | {t_vr:.1f}s")

    # ③ 市场环境（实时）
    valid_chg = pd.to_numeric(df["change_pct"], errors="coerce").dropna()
    market_advance_ratio = float((valid_chg > 0).mean())
    market_median = float(valid_chg.median())
    if market_advance_ratio < 0.45:
        min_score, max_tail_risk, market_mode = 45, 1, "弱市模式"
    elif market_advance_ratio > 0.60:
        min_score, max_tail_risk, market_mode = 40, 2, "强势模式"
    else:
        min_score, max_tail_risk, market_mode = 40, 1, "正常模式"
    log(f"③ 市场环境：上涨占比 {market_advance_ratio:.1%} | 中位数 {market_median:+.2f}% | {market_mode}")

    ind_chg_full = df.groupby("industry")["change_pct"].mean().sort_values(ascending=False)

    # ④ 流动性过滤
    df = df[pd.to_numeric(df["amount"], errors="coerce") >= 50_000_000]
    df = df[pd.to_numeric(df["circ_mv"], errors="coerce") >= 3_000_000_000]
    tor = pd.to_numeric(df["turnover_rate"], errors="coerce")
    df = df[(tor >= 0.5) & (tor <= 15.0)]
    log(f"④ 流动性过滤：剩余 {len(df)} 只")

    # ⑤ 指标并行计算（官方日线缓存 + 排除当日 bar，与官方管道口径一致）
    t0 = time.perf_counter()
    ind_df = compute_indicators_parallel(df, official_daily_cache_root(args.data_root),
                                         today_str, args.workers)
    t_ind = time.perf_counter() - t0
    log(f"⑥ 指标计算：{len(ind_df)} 只完成 | {t_ind:.1f}s")
    df = df.merge(ind_df, on="code", how="left")
    df = df.dropna(subset=["ma5", "ma10", "ma20", "ma60", "rsi14", "atr14_pct", "boll_position"])
    log(f"   指标齐全：{len(df)} 只")

    # ⑦ 趋势过滤
    df["trend_aligned"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["ma20"] > df["ma60"])
    df["trend_near"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])
    df = df[df["trend_near"]]
    log(f"⑦ 趋势过滤（MA5>MA10>MA20）：剩余 {len(df)} 只")

    # ⑧ 双轴评分 + 买卖区
    scores = df.apply(lambda r: calc_opportunity_score(r, ind_chg_full), axis=1)
    df["opportunity_score"] = [s[0] for s in scores]
    df["opportunity_tags"] = [s[1] for s in scores]
    df["tail_risk_tags"] = df.apply(calc_tail_risk, axis=1)
    df["tail_risk_count"] = df["tail_risk_tags"].apply(len)
    df["industry_chg"] = df["industry"].map(ind_chg_full)
    df = pd.concat([df, df.apply(calc_zones, axis=1)], axis=1)

    candidates = df[
        (df["opportunity_score"] >= min_score)
        & (df["tail_risk_count"] <= max_tail_risk)
        & (df["rr_ratio"] >= 1.5)
        & (df["industry_chg"] > 0)
    ].sort_values("opportunity_score", ascending=False)
    official = candidates[
        (candidates["opportunity_score"] >= 55) & (candidates["tail_risk_count"] == 0)
    ].head(20).copy()
    official["fast_entry"] = official["opportunity_score"] >= 85
    watch = candidates[~candidates.index.isin(official.index)].head(15)
    log(f"⑧ 评分完成：候选 {len(candidates)} 只 | 正式 {len(official)} 只 | 观察 {len(watch)} 只")

    # ⑨ 输出
    fast_dir = args.data_root / "cache" / "fast"
    fast_dir.mkdir(parents=True, exist_ok=True)
    csv_path = fast_dir / f"scan_result_{observed_at.strftime('%Y%m%d')}_{observed_at.strftime('%H%M')}.csv"
    output_cols = ["code", "name", "market", "price", "change_pct", "volume_ratio", "turnover_rate",
                   "amount", "circ_mv", "industry", "industry_chg", "concepts",
                   "opportunity_score", "tail_risk_count",
                   "buy_low", "buy_high", "chase_line", "protect",
                   "sell1_low", "sell1_high", "sell2_low", "sell2_high",
                   "rr_ratio", "rsi14", "atr14_pct", "boll_position",
                   "trend_aligned", "return_5d", "return_20d"]
    out_df = candidates[[c for c in output_cols if c in candidates.columns]].copy()
    out_df["opportunity_tags"] = candidates["opportunity_tags"].apply(lambda x: "|".join(x))
    out_df["tail_risk_tags"] = candidates["tail_risk_tags"].apply(lambda x: "|".join(x))
    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    total_sec = time.perf_counter() - t_start
    html_path = args.html or (REPORT_DIR / f"全盘T1策略扫描报告_{observed_at.strftime('%Y%m%d')}_{phase}.html")
    ctx = {
        "date": today_str, "phase": phase,
        "generated_at": observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        "snap_source": snap_source, "snap_obs": snap_obs,
        "total_sec": total_sec, "t_snap": t_snap, "t_vr": t_vr, "t_ind": t_ind,
        "advance_ratio": market_advance_ratio, "median": market_median, "mode": market_mode,
        "funnel": f"{initial} → 硬{after_hard} → 候选{len(candidates)} → 正式{len(official)}",
        "ind_top": list(ind_chg_full.head(5).items()),
        "ind_bottom": list(ind_chg_full.tail(5).items())[::-1],
        "byd": byd_info,
        "top5": official.head(5).to_dict("records"),
        "official": official.to_dict("records"), "n_official": len(official),
        "watch": watch.to_dict("records"), "n_watch": len(watch),
    }
    try:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_html(ctx), encoding="utf-8")
    except OSError as exc:
        log(f"HTML 写入失败: {exc}")

    # 控制台摘要
    print("\n" + "=" * 92)
    print(f"全盘 T+1 快速扫描 | {today_str} {phase} | {market_mode} | 总耗时 {total_sec:.0f}s")
    print(f"上涨占比 {market_advance_ratio:.1%} | 中位数 {market_median:+.2f}% | "
          f"BYD {byd_info['chg']:+.2f}%（{'触发' if byd_info['triggered'] else '未触发'}）")
    print("=" * 92)
    for i, (_, row) in enumerate(official.iterrows(), 1):
        fast = " [快速介入]" if row["fast_entry"] else ""
        print(f"{i:2d}. {row['code']} {row['name']} | {row['price']:.2f} | {row['change_pct']:+.2f}% | "
              f"评分{row['opportunity_score']:.0f}{fast} | RR{row['rr_ratio']:.2f} | "
              f"买区{row['buy_low']:.2f}-{row['buy_high']:.2f} | 保护{row['protect']:.2f} | "
              f"禁追{row['chase_line']:.2f} | {row.get('industry', '')}({row['industry_chg']:+.1f}%)")
    print(f"\n结果 CSV: {csv_path}")
    print(f"HTML 报告: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

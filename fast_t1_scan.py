# -*- coding: utf-8 -*-
r"""
fast_t1_scan.py — 全盘 T+1 策略快速扫描（一键）

设计目标：绕开官方管道的审计/分类/分钟数据等重步骤，
只做扫描真正需要的三件事：快照 → 指标（同遍量比） → 评分出报告。

提速要点（v3，2026-08-12）：
  1. 快照单源快速模式：sina 主源一次采集，跳过参考源分页抓取
     （扫描所需的量/额/换手/市值字段 sina 已齐备，行业字段由
     ensure_industry 从最近官方 run 回补），实测约省 30 秒
  2. 快照新鲜度复用：同日快照若 < --fresh 分钟直接复用，否则实时采集
  3. 量比并入指标单遍：5 日均量在指标计算读取同一份日线 JSON 时顺带
     算出（口径与 marketbase.volume_ratio._avg_5d_volume 完全一致），
     不再有独立的量比阶段，省去数千次重复 JSON 读取
  4. 指标并行计算：只对流动性过滤后的候选并行读取日线缓存并现算指标，
     线程池 48、chunksize 16；且用快照最新价补丁当日 bar
     （收盘后运行即为收盘口径）
  5. 全程无网络依赖的审计/分类步骤，不会被挂起阻塞

用法：
    python fast_t1_scan.py                     # 默认：快照 15 分钟内复用，否则新采
    python fast_t1_scan.py --fresh 0           # 强制重新采集快照
    python fast_t1_scan.py --workers 32        # 并行线程数（默认 24，指标池提升至 48）
    python fast_t1_scan.py --html D:\x\y.html  # 指定报告输出路径

输出：
    {项目根}\data\cache\fast\scan_result_{日期}_{时分}.csv   候选明细
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
from marketbase.indicators import compute_daily_indicators, compute_rps20  # noqa: E402
from marketbase.volume_ratio import elapsed_trade_minutes  # noqa: E402
from marketbase.snapshot import fetch_cn_snapshot  # noqa: E402
from marketbase.realtime_window import fetch_tencent_quotes  # noqa: E402
from strategies.full_market_t1 import build_candidate_union, write_candidate_union  # noqa: E402

REPORT_DIR = MB_ROOT / "reports"  # HTML 报告默认输出目录（可用 --html 覆盖）

IND_FIELDS = ["ma5", "ma10", "ma11", "ma20", "ma23", "ma60", "rsi14", "atr14", "atr14_pct",
              "boll_upper", "boll_middle", "boll_lower", "boll_position",
              "return_5d", "return_10d", "return_20d",
              "upper_shadow_ratio", "lower_shadow_ratio", "repeated_upper_shadow",
              "momentum_delta_1", "momentum_delta_3", "input_rows"]

CN_TZ = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(f"[{datetime.now(CN_TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_realtime_codes(value: str) -> list[str]:
    """解析实时查询代码，兼容逗号、分号和空格，去重并保持用户输入顺序。"""
    import re

    codes: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\s]+", value.strip()):
        if item.isdigit() and len(item) <= 6:
            code = item.zfill(6)
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


def filter_realtime_quotes(codes: list[str], quotes: pd.DataFrame) -> pd.DataFrame:
    """按请求顺序返回实时行情；缺失代码明确标记，避免静默漏股。"""
    frame = quotes.copy()
    if "code" not in frame.columns:
        frame["code"] = ""
    frame["code"] = frame["code"].astype(str).str.strip().str.zfill(6)
    frame = frame.drop_duplicates("code", keep="last").set_index("code")
    rows: list[dict[str, object]] = []
    for code in codes:
        if code in frame.index:
            row = frame.loc[code].to_dict()
            row["code"] = code
            row["realtime_status"] = "已获取"
        else:
            row = {"code": code, "realtime_status": "未找到"}
        rows.append(row)
    return pd.DataFrame(rows)


def load_bse_codes(data_root: Path) -> list[str]:
    """从本地最近的完整快照读取北交所证券主表，不依赖慢参考源。"""
    paths = [
        data_root / "daily_runs" / "cache" / "market_snapshot.json",
        data_root / "cache" / "market_snapshot.json",
    ]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows", [])
            codes = {
                str(row.get("code", "")).strip().zfill(6)
                for row in rows
                if isinstance(row, dict)
            }
            result = sorted(code for code in codes if code[:1] in {"4", "8", "9"})
            if result:
                return result
        except (OSError, ValueError, TypeError):
            continue
    return []


def _market_of(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return ""


def query_realtime_quotes(codes: list[str], *, shsz_fetcher=None, bj_fetcher=None) -> pd.DataFrame:
    """重新采集一次实时行情并返回指定股票的行情（按输入顺序）。

    沪深走 sina 快照链路；北交所使用独立的腾讯行情链路采集，
    北交所单只/整批失败只标记失败，不影响沪深结果（需求 7.4）。
    """
    if not codes:
        return pd.DataFrame(columns=["code", "realtime_status"])
    bj_codes = [code for code in codes if code[:1] in {"4", "8", "9"}]
    shsz_codes = [code for code in codes if code[:1] not in {"4", "8", "9"}]
    frames: list[pd.DataFrame] = []
    if shsz_codes:
        fetch_shsz = shsz_fetcher or (lambda: fetch_cn_snapshot("sina"))
        frames.append(filter_realtime_quotes(shsz_codes, fetch_shsz()))
    if bj_codes:
        fetch_bj = bj_fetcher or fetch_tencent_quotes
        try:
            bj_frame, _errors = fetch_bj(bj_codes)
            frames.append(filter_realtime_quotes(bj_codes, bj_frame))
        except Exception as exc:  # noqa: BLE001 - 北交所失败不得隐藏沪深成功结果。
            log(f"北交所实时采集失败：{exc}")
            frames.append(pd.DataFrame([{"code": code, "realtime_status": "获取失败"} for code in bj_codes]))
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["code", "realtime_status"])
    order = {code: idx for idx, code in enumerate(codes)}
    merged["_order"] = merged["code"].map(order)
    merged = merged.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)
    merged["market"] = merged["code"].map(_market_of)

    # 统一补齐采集时间与行情时间（需求 7.1）
    observed_at = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    if "observed_at" not in merged.columns:
        merged["observed_at"] = ""
    merged["observed_at"] = merged["observed_at"].fillna("").astype(str)
    merged.loc[merged["observed_at"].str.strip().eq(""), "observed_at"] = observed_at
    if "quote_time" not in merged.columns:
        merged["quote_time"] = ""
    merged["quote_time"] = merged["quote_time"].fillna("").astype(str)
    blank_qt = merged["quote_time"].str.strip().eq("")
    if blank_qt.any() and "ticktime" in merged.columns:
        today = observed_at[:10]
        tick = merged["ticktime"].fillna("").astype(str).str.strip()
        merged.loc[blank_qt, "quote_time"] = tick.map(lambda t: f"{today} {t}" if t else "")[blank_qt]
    blank_qt = merged["quote_time"].str.strip().eq("")
    merged.loc[blank_qt, "quote_time"] = observed_at
    return merged


def print_realtime_quotes(frame: pd.DataFrame) -> None:
    columns = ["code", "market", "name", "price", "change_pct", "volume", "amount",
               "quote_time", "observed_at", "realtime_status"]
    available = [column for column in columns if column in frame.columns]
    print("\n实时行情查询结果")
    print(frame[available].to_string(index=False))


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

def _read_cached_bse_audit(data_root: Path) -> dict:
    """从官方快照缓存读取北交所覆盖审计（复用缓存快照时仍可追溯）。"""
    try:
        payload = json.loads((data_root / "cache" / "market_snapshot.json").read_text(encoding="utf-8"))
        return dict((payload.get("audit") or {}).get("bse_audit") or {})
    except (OSError, ValueError, TypeError):
        return {}


def get_snapshot(data_root: Path, observed_at: datetime, fresh_minutes: int) -> tuple[pd.DataFrame, str, float, dict]:
    """返回 (快照 DataFrame, 来源说明, 耗时秒, 北交所覆盖审计)。

    新采时使用"单源快速模式"：仅用 sina 主源，跳过参考源分页抓取。
    快扫需要的 volume/amount/换手率/市值字段 sina 均提供；
    pe/pb/行业等参考源字段中，行业由 ensure_industry 回补，pe/pb 扫描不使用。
    """
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
                return cached, f"复用缓存快照（{age_min:.0f} 分钟前）", 0.0, _read_cached_bse_audit(data_root)
        except Exception:
            pass  # 缓存损坏则重新采集

    t0 = time.perf_counter()

    def progress(msg: str) -> None:
        log(f"  快照采集: {msg}")

    result = collect_market_snapshot(
        cache_path=data_root / "cache" / "market_snapshot.json",
        now=observed_at,
        progress=progress,
        reference_fetcher=lambda: pd.DataFrame(),  # 单源快速模式：跳过参考源（build_live_snapshot 对空参考源直接跳过补全）
        bse_codes=load_bse_codes(data_root),
    )
    df = result.frame.copy()
    df["code"] = df["code"].astype(str).str.strip().str.zfill(6)
    df.to_csv(snap_csv, index=False, encoding="utf-8-sig")
    bse_audit = dict(result.audit.get("bse_audit") or {})
    return df, "实时采集（单源快速模式）", time.perf_counter() - t0, bse_audit


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


# ────────────────────────── 量比（5日均量，与指标同遍计算） ──────────────────────────

def _avg5d_from_payload(payload: dict, cutoff_iso: str) -> float | None:
    """从日线 JSON payload 直接计算最近 5 个完整交易日均量。

    口径与 marketbase.volume_ratio._avg_5d_volume 完全一致：
      - 排除观察日当日及之后的 bar（cutoff）
      - volume <= 0 的行跳过
      - 缓存为"手"单位（volume_unit == "shou" 或 source == "tencent"
        且 volume_unit != "shares"）时 ×100 换算为股
      - 不足 5 个有效交易日返回 None
    """
    rows = payload.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return None
    volume_unit = payload.get("volume_unit", "")
    source = payload.get("source", "")
    needs_shou_conversion = (
        volume_unit != "shares"
        and (volume_unit == "shou" or source == "tencent")
    )
    volumes: list[float] = []
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        row_date = row.get("date")
        if isinstance(row_date, str) and row_date >= cutoff_iso:
            continue
        raw = row.get("volume")
        try:
            vol = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            vol = None
        if vol is not None and vol == vol and vol > 0:
            if needs_shou_conversion:
                vol = vol * 100.0  # 手 → 股
            volumes.append(vol)
        if len(volumes) >= 5:
            break
    if len(volumes) < 5:
        return None
    return sum(volumes) / len(volumes)


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
        # 量比同遍计算：复用已加载的 payload，零额外 IO
        record["avg5d"] = _avg5d_from_payload(payload, today_str)
        return record
    except Exception:
        return None


def compute_full_market_indicators(
    df: pd.DataFrame,
    daily_root: Path,
    today_str: str,
    workers: int,
    fast_dir: Path,
) -> pd.DataFrame:
    """Use the optional workspace cache while remaining valid in a clean checkout."""
    cache_loader = globals().get("load_or_compute_indicators")
    if callable(cache_loader):
        return cache_loader(df, daily_root, today_str, workers, fast_dir)
    return compute_indicators_parallel(df, daily_root, today_str, workers)


def map_full_market_rps20(
    candidate_indicators: pd.DataFrame,
    full_market_indicators: pd.DataFrame,
) -> pd.DataFrame:
    """Map RPS20 ranked on the complete tradable main-board cross-section."""
    result = candidate_indicators.copy()
    result["rps20"] = result["code"].astype(str).str.zfill(6).map(
        compute_rps20(full_market_indicators)
    )
    return result


def select_rps20_universe(eligible_main_board: pd.DataFrame) -> pd.DataFrame:
    """Keep the full tradable board before any price or liquidity threshold."""
    volume = pd.to_numeric(eligible_main_board["volume"], errors="coerce").fillna(0)
    return eligible_main_board[volume > 0].copy()


def compute_indicators_parallel(df: pd.DataFrame, daily_root: Path,
                                today_str: str, workers: int) -> pd.DataFrame:
    codes = df["code"].tolist()
    records: list[dict] = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(workers, 48)) as pool:
        for i, item in enumerate(pool.map(
                lambda c: _indicator_record(c, daily_root, today_str),
                codes, chunksize=16), 1):
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
    bse = ctx.get("bse") or {}
    bse_text = (
        f" ｜ 北交所覆盖 {bse.get('bj_actual', 0)}/{bse.get('bj_expected', 0)}"
        f"（缺失 {bse.get('bj_missing', 0)}，来源 {bse.get('source', '未知')}）"
        if bse
        else " ｜ 北交所覆盖：无本次采集审计"
    )
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
<div class="meta">生成时间 {ctx['generated_at']} ｜ 快照 {ctx['snap_source']}（{ctx['snap_obs']}）{bse_text} ｜ 全流程耗时 <b>{ctx['total_sec']:.0f} 秒</b>
（快照 {ctx['t_snap']:.0f}s / 指标+量比 {ctx['t_ind']:.0f}s）｜ 快速扫描模式 v3（单源快照 + 指标量比同遍，无审计网络步骤）</div>

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


def _snapshot_observed_iso8601(value: object) -> str:
    observed = pd.to_datetime(value)
    if observed.tzinfo is None:
        observed = observed.tz_localize(CN_TZ)
    return observed.isoformat()


# ────────────────────────── 主流程 ──────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="全盘 T+1 策略快速扫描")
    parser.add_argument("--data-root", type=Path, default=MB_ROOT / "data")
    parser.add_argument("--fresh", type=int, default=15, help="快照复用新鲜度（分钟），0=强制新采")
    parser.add_argument("--workers", type=int, default=24, help="并行线程数")
    parser.add_argument("--html", type=Path, default=None, help="HTML 报告输出路径")
    parser.add_argument("--realtime", type=str, default=None, help="仅查询指定股票实时行情，代码用逗号或空格分隔")
    args = parser.parse_args()

    if args.realtime is not None:
        codes = parse_realtime_codes(args.realtime)
        if not codes:
            parser.error("--realtime 未解析出有效的 6 位股票代码")
        try:
            realtime = query_realtime_quotes(codes)
        except Exception as exc:  # noqa: BLE001 - expose source failure to the operator.
            print(f"实时行情采集失败：{exc}", file=sys.stderr)
            return 1
        print_realtime_quotes(realtime)
        return 0

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

    # ① 快照（单源快速模式）
    df, snap_source, t_snap, bse_audit = get_snapshot(args.data_root, observed_at, args.fresh)
    snap_obs = str(df["observed_at"].iloc[0])[:19]
    bj_info = (
        f" | 北交所覆盖 {bse_audit.get('bj_actual', 0)}/{bse_audit.get('bj_expected', 0)}"
        f"（缺失 {bse_audit.get('bj_missing', 0)}）"
        if bse_audit
        else ""
    )
    log(f"① 快照就绪：{len(df)} 只 | {snap_source}{bj_info} | {t_snap:.1f}s")
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
    # v2.1 (2026-09-03): 用户指令型规则——剔除创业板(30)/科创板(68)，仅沪主板(60)/深主板(00)参与
    code_str = df["code"].astype(str).str.zfill(6)
    df = df[code_str.str.startswith(("60", "00"))]
    if "listed_days" in df.columns:
        ld = pd.to_numeric(df["listed_days"], errors="coerce")
        df = df[(ld.isna()) | (ld >= 60)]

    # RPS20 母集合同候选价格/流动性门槛解耦：先对完整可交易主板计算日线与横截面排名。
    rps_universe_df = select_rps20_universe(df)
    t0 = time.perf_counter()
    fast_dir = args.data_root / "cache" / "fast"
    full_ind_df = compute_full_market_indicators(
        rps_universe_df,
        official_daily_cache_root(args.data_root),
        today_str,
        args.workers,
        fast_dir,
    )
    lifecycle_indicator_fields = {
        "ma11", "ma23", "momentum_delta_1", "momentum_delta_3", "repeated_upper_shadow"
    }
    if not lifecycle_indicator_fields.issubset(full_ind_df.columns):
        # 旧版同日缓存不含生命周期字段时从完整母集原始日线重算。
        full_ind_df = compute_indicators_parallel(
            rps_universe_df, official_daily_cache_root(args.data_root), today_str, args.workers
        )
    full_ind_df = map_full_market_rps20(full_ind_df, full_ind_df)

    # 正式下限仍为 50 元；40–49.99 元保留到候选并集，由价格带分类强制影子化。
    df = df[pd.to_numeric(df["price"], errors="coerce").fillna(0) >= 40]
    df = select_rps20_universe(df)
    df["industry"] = df["industry"].fillna("未知") if "industry" in df.columns else "未知"
    after_hard = len(df)
    log(f"② 硬过滤：{initial} -> {after_hard}" + "（仅主板 + 现价≥40；40–49.99 仅影子）")

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

    # ⑤ 指标并行计算（官方日线缓存 + 排除当日 bar，与官方管道口径一致；
    #    量比的 5 日均量在同一次 JSON 读取中顺带算出，不再有独立量比阶段）
    candidate_codes = set(df["code"].astype(str).str.zfill(6))
    ind_df = full_ind_df[full_ind_df["code"].astype(str).str.zfill(6).isin(candidate_codes)].copy()
    avg5d = ind_df.set_index("code")["avg5d"]
    df = df.merge(ind_df, on="code", how="left")
    df = df.dropna(subset=["ma5", "ma10", "ma20", "ma60", "rsi14", "atr14_pct", "boll_position"])
    df = apply_volume_ratio(df, avg5d, observed_at)
    t_ind = time.perf_counter() - t0
    vr_cov = int(df["volume_ratio"].notna().sum())
    log(f"⑤ 指标+量比：{len(ind_df)} 只完成 | 量比覆盖 {vr_cov}/{len(df)} | {t_ind:.1f}s")

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
    production_candidates = candidates[pd.to_numeric(candidates["price"], errors="coerce") >= 50]
    official = production_candidates[
        (production_candidates["opportunity_score"] >= 55) & (production_candidates["tail_risk_count"] == 0)
    ].head(20).copy()
    official["fast_entry"] = official["opportunity_score"] >= 85
    watch = production_candidates[~production_candidates.index.isin(official.index)].head(15)
    shadow_count = int((pd.to_numeric(candidates["price"], errors="coerce") < 50).sum())
    log(
        f"⑧ 评分完成：候选并集 {len(candidates)} 只（影子 {shadow_count}）"
        f" | 正式 {len(official)} 只 | 观察 {len(watch)} 只"
    )

    # ⑨ 输出
    fast_dir = args.data_root / "cache" / "fast"
    fast_dir.mkdir(parents=True, exist_ok=True)
    csv_path = fast_dir / f"scan_result_{observed_at.strftime('%Y%m%d')}_{observed_at.strftime('%H%M')}.csv"
    output_cols = ["code", "name", "market", "price", "change_pct", "volume_ratio", "turnover_rate",
                   "amount", "circ_mv", "industry", "industry_chg", "concepts",
                   "opportunity_score", "tail_risk_count",
                   "buy_low", "buy_high", "chase_line", "protect",
                   "sell1_low", "sell1_high", "sell2_low", "sell2_high",
                   "rr_ratio", "rsi14", "rps20", "atr14_pct", "boll_position",
                   "trend_aligned", "return_5d", "return_20d", "ma11", "ma23",
                   "momentum_delta_1", "momentum_delta_3", "repeated_upper_shadow"]
    out_df = production_candidates[[c for c in output_cols if c in production_candidates.columns]].copy()
    out_df["opportunity_tags"] = production_candidates["opportunity_tags"].apply(lambda x: "|".join(x))
    out_df["tail_risk_tags"] = production_candidates["tail_risk_tags"].apply(lambda x: "|".join(x))
    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    funnel_counts = {
        "initial": initial,
        "after_hard": after_hard,
        "candidates": len(candidates),
        "official": len(official),
        "watch": len(watch),
    }
    candidate_observed_at = _snapshot_observed_iso8601(snap_obs or observed_at)
    candidate_dt = pd.to_datetime(candidate_observed_at)
    candidate_union_path = fast_dir / (
        f"candidate_union_{candidate_dt.strftime('%Y%m%d')}_{candidate_dt.strftime('%H%M')}.json"
    )
    candidate_union = build_candidate_union(
        candidates.copy(),
        trade_date=today_str,
        observed_at=candidate_observed_at,
        market_rows=initial,
        funnel=funnel_counts,
    )
    write_candidate_union(candidate_union, candidate_union_path)
    log(f"候选并集: {candidate_union_path}")

    total_sec = time.perf_counter() - t_start
    html_path = args.html or (REPORT_DIR / f"全盘T1策略扫描报告_{observed_at.strftime('%Y%m%d')}_{phase}.html")
    ctx = {
        "date": today_str, "phase": phase,
        "generated_at": observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        "snap_source": snap_source, "snap_obs": snap_obs, "bse": bse_audit,
        "total_sec": total_sec, "t_snap": t_snap, "t_ind": t_ind,
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
    print(f"候选并集: {candidate_union_path}")
    print(f"HTML 报告: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

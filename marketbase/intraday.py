"""全市场分钟快照 —— 盘中连续采集与盘后分钟序列构建.

基于每分钟全市场快照拼成 intraday_1m.parquet，支持:
  - 全日 VWAP (基于累计量额)
  - 锚定 VWAP (从指定时刻起的 VWAP)
  - 近 N 分钟量能变化
  - 分钟数据质量审计
  - 分钟摆动高/低点 (named pivot)
  - 开盘代理价 (开盘集合竞价或第一笔成交)
  - 午后摆动低点 (13:00 后最近摆动低点)
  - 分钟客观事实字段 (午后摆动低点、距日高偏离、近N分钟成交额、涨跌停触及等)

用于盘中连续性确认（如连续3分钟站稳VWAP）和尾盘确认.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd


# ── 分钟数据质量审计 ─────────────────────────────────────────────────


def _trading_minutes_between(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[pd.Timestamp]:
    """生成 start 到 end 之间的所有交易分钟（排除午休 11:30-13:00，不含终点）."""
    minutes: list[pd.Timestamp] = []
    current = start.floor("min")
    end_floor = end.floor("min")
    lunch_start = pd.Timestamp("11:30").time()
    lunch_end = pd.Timestamp("13:00").time()
    while current < end_floor:
        t = current.time()
        if not (lunch_start <= t < lunch_end):
            minutes.append(current)
        current += pd.Timedelta(minutes=1)
    return minutes


def _expected_minutes_count(start: str, end: str) -> int:
    """计算预期交易分钟数，排除午休 11:30-13:00.

    示例:
      09:30-15:00 → 240 (上午 120 + 下午 120)
      13:00-15:00 → 120 (仅下午)
      09:30-11:30 → 120 (仅上午)
    """
    try:
        s_h, s_m = map(int, start.split(":"))
        e_h, e_m = map(int, end.split(":"))

        lunch_start = 11 * 60 + 30  # 11:30
        lunch_end = 13 * 60         # 13:00

        start_min = s_h * 60 + s_m
        end_min = e_h * 60 + e_m

        # 上午段: start 到 min(11:30, end)
        morning = max(0, min(lunch_start, end_min) - max(start_min, 0))
        # 下午段: max(13:00, start) 到 end
        afternoon = max(0, end_min - max(lunch_end, start_min))

        return morning + afternoon
    except (ValueError, AttributeError):
        return 240


def _group_missing_periods(
    missing: list[pd.Timestamp],
) -> list[dict[str, str]]:
    """将缺失分钟列表合并为连续时段."""
    if not missing:
        return []
    periods: list[dict[str, str]] = []
    start = missing[0]
    prev = missing[0]
    for t in missing[1:]:
        gap = (t - prev).total_seconds()
        if gap > 60:
            periods.append({"start": str(start), "end": str(prev), "count": len(periods) + 1})
            start = t
        prev = t
    periods.append({"start": str(start), "end": str(prev), "count": len(periods)})
    return periods


def audit_minute_sequence(
    frame: pd.DataFrame,
    *,
    expected_start: str = "09:30",
    expected_end: str = "15:00",
    observed_at: datetime | None = None,
    all_codes: list[str] | None = None,
) -> dict[str, object]:
    """审计分钟序列的质量.

    检查:
      - 覆盖时间范围
      - 缺失分钟（排除午休 11:30-13:00，以 observed_at 为审计终点）
      - 时间连续性断点
      - 每只股票的分钟覆盖率
      - 完全无数据的代码
    """
    audit: dict[str, object] = {
        "generated_at": (observed_at or datetime.now().astimezone()).isoformat(),
        "expected_start": expected_start,
        "expected_end": expected_end,
    }

    if frame.empty:
        audit["error"] = "empty frame"
        if all_codes:
            audit["codes_with_no_data"] = all_codes
            audit["codes_with_no_data_count"] = len(all_codes)
        return audit

    # 时间列
    time_col = None
    for col in ("time", "minute", "timestamp"):
        if col in frame.columns:
            time_col = col
            break

    if time_col is None:
        audit["error"] = "no time column found"
        return audit

    times = pd.to_datetime(frame[time_col], errors="coerce")
    valid_times = times.dropna().sort_values()

    # 动态预期分钟数：基于观测时间确定当前交易时段
    if observed_at is not None:
        obs_cn = observed_at.astimezone(timezone(timedelta(hours=8)))
        obs_time = obs_cn.time()
        lunch_start = pd.Timestamp("11:30").time()
        lunch_end = pd.Timestamp("13:00").time()
        if obs_time >= lunch_end:
            # 下午时段：13:00 到观测时间
            dynamic_start = "13:00"
            dynamic_end = obs_cn.strftime("%H:%M")
        elif obs_time >= lunch_start:
            # 午休期间：仅上午时段
            dynamic_start = "09:30"
            dynamic_end = "11:30"
        else:
            # 上午时段：09:30 到观测时间
            dynamic_start = "09:30"
            dynamic_end = obs_cn.strftime("%H:%M")
    else:
        dynamic_start = valid_times.min().strftime("%H:%M") if not valid_times.empty else expected_start
        dynamic_end = valid_times.max().strftime("%H:%M") if not valid_times.empty else expected_end
    dynamic_expected = _expected_minutes_count(dynamic_start, dynamic_end)
    full_day_expected = _expected_minutes_count(expected_start, expected_end)

    audit["total_expected_minutes"] = full_day_expected
    audit["expected_minutes_dynamic"] = dynamic_expected
    audit["actual_minutes"] = int(valid_times.nunique())
    audit["latest_minute_time"] = str(valid_times.max()) if not valid_times.empty else None
    audit["earliest_minute_time"] = str(valid_times.min()) if not valid_times.empty else None

    # 缺失分钟（以 observed_at 为审计终点，排除午休）
    if not valid_times.empty:
        data_max = valid_times.max()
        if observed_at is not None:
            obs_cn = observed_at.astimezone(timezone(timedelta(hours=8)))
            # 审计终点：当前观测时间，但不超过交易结束时间 15:00
            trading_end_cn = obs_cn.replace(hour=15, minute=0, second=0, microsecond=0)
            if obs_cn > trading_end_cn:
                audit_end = pd.Timestamp(trading_end_cn.timestamp(), unit="s", tz=data_max.tz)
            else:
                audit_end = pd.Timestamp(obs_cn.timestamp(), unit="s", tz=data_max.tz)
        else:
            audit_end = data_max
        trading_minutes = _trading_minutes_between(valid_times.min(), audit_end)
        present_minutes = set(valid_times.dt.floor("min"))
        missing = sorted(set(trading_minutes) - present_minutes)
    else:
        missing = []
    audit["missing_minutes"] = [str(m) for m in missing[:20]]
    audit["missing_minute_count"] = len(missing)

    # 缺失时段（连续缺失合并）
    audit["missing_periods"] = _group_missing_periods(missing)[:20]

    # 时间连续性断点 (>2分钟间隔，排除午休)
    if len(valid_times) > 1:
        sorted_times = valid_times.sort_values().reset_index(drop=True)
        gaps = sorted_times.diff().dropna()
        # 排除午休边界的正常断点（11:30→13:00 = 90分钟）
        lunch_gap = timedelta(minutes=90)
        breaks = gaps[(gaps > timedelta(minutes=2)) & (gaps != lunch_gap)]
        audit["time_continuity_breaks"] = [
            {"after": str(sorted_times.iloc[i]), "gap_seconds": gap.total_seconds()}
            for i, gap in breaks.items()
        ][:20]
        audit["continuity_break_count"] = len(breaks)
    else:
        audit["continuity_break_count"] = 0
        audit["time_continuity_breaks"] = []

    # 每只股票的分钟覆盖率
    if "code" in frame.columns:
        code_time_counts = frame.groupby("code")[time_col].nunique()
        total_minutes = max(dynamic_expected, 1)
        coverage = (code_time_counts / total_minutes).describe().to_dict()
        audit["code_coverage"] = {
            "mean": round(coverage.get("mean", 0), 4),
            "min": round(coverage.get("min", 0), 4),
            "max": round(coverage.get("max", 0), 4),
            "codes_full": int((code_time_counts >= total_minutes * 0.95).sum()),
            "codes_partial": int(((code_time_counts < total_minutes * 0.95) & (code_time_counts > 0)).sum()),
            "codes_none": int((code_time_counts == 0).sum()),
        }
        # 逐代码覆盖率
        audit["minute_coverage_by_code"] = [
            {"code": str(c), "minutes": int(v), "coverage_pct": round(float(v) / total_minutes * 100, 1)}
            for c, v in code_time_counts.items()
        ]
        # 完全无数据的代码
        present_codes = set(frame["code"].unique())
        if all_codes:
            missing_codes = sorted(set(all_codes) - present_codes)
            audit["codes_with_no_data"] = missing_codes[:200]
            audit["codes_with_no_data_count"] = len(missing_codes)
        else:
            audit["codes_with_no_data"] = []
            audit["codes_with_no_data_count"] = 0

    return audit


# ── 分钟序列构建 ─────────────────────────────────────────────────────


def append_minute_snapshot(
    snapshot_df: pd.DataFrame,
    output_path: str | Path,
    *,
    time_col: str = "observed_at",
    minute_col: str = "time",
) -> None:
    """将单次快照追加到分钟序列 parquet 文件.

    在盘中每分钟调用一次，逐步累积全市场分钟快照.
    """
    path = Path(output_path)
    minute_df = snapshot_df.copy()

    # 确保时间列
    if minute_col not in minute_df.columns:
        if time_col in minute_df.columns:
            minute_df[minute_col] = pd.to_datetime(minute_df[time_col], errors="coerce")
        else:
            now = datetime.now().astimezone()
            minute_df[minute_col] = pd.Timestamp(now)

    # 只保留关键字段
    keep_cols = ["code", minute_col, "price", "volume", "amount", "turnover_rate"]
    keep_cols = [c for c in keep_cols if c in minute_df.columns]
    minute_df = minute_df[keep_cols].copy()

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, minute_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["code", minute_col], keep="last")
        combined.to_parquet(path, index=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        minute_df.to_parquet(path, index=False)


def build_intraday_sequence(
    input_path: str | Path,
) -> pd.DataFrame:
    """从累积的分钟快照构建完整分钟序列.

    返回包含 code, time, price, volume, amount 的 DataFrame.
    """
    path = Path(input_path)
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_parquet(path)
    return df.sort_values(["code", "time"]).reset_index(drop=True)


# ── 盘中指标计算 ─────────────────────────────────────────────────────


def compute_intraday_metrics(
    minute_df: pd.DataFrame,
    *,
    anchor_time: str | None = None,
) -> pd.DataFrame:
    """从分钟序列计算盘中指标.

    返回每只股票在每个分钟点的指标:
      - vwap: 全日 VWAP (基于累计量额)
      - anchored_vwap: 锚定 VWAP (从 anchor_time 起)
      - vol_3m_delta: 近3分钟量能变化 (pct)
      - vol_5m_delta: 近5分钟量能变化 (pct)
      - price_vs_vwap: 价格相对于 VWAP 的偏离
    """
    if minute_df.empty:
        return pd.DataFrame()

    df = minute_df.copy()
    # 确保数值列
    for col in ("price", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    time_col = "time"
    if time_col not in df.columns:
        return df

    df = df.sort_values(["code", time_col]).reset_index(drop=True)

    results: list[pd.DataFrame] = []
    for code, group in df.groupby("code"):
        group = group.sort_values(time_col).copy()

        # 累计量额
        cum_vol = group["volume"].cumsum()
        cum_amt = group["amount"].cumsum()

        # 全日 VWAP
        group["vwap"] = cum_amt / cum_vol.replace(0, float("nan"))

        # 锚定 VWAP
        if anchor_time is not None:
            anchor_mask = group[time_col] >= anchor_time
            anchor_vol = group.loc[anchor_mask, "volume"].cumsum()
            anchor_amt = group.loc[anchor_mask, "amount"].cumsum()
            group["anchored_vwap"] = float("nan")
            group.loc[anchor_mask, "anchored_vwap"] = (
                anchor_amt / anchor_vol.replace(0, float("nan"))
            )
        else:
            group["anchored_vwap"] = float("nan")

        # 近N分钟量能变化
        group["vol_3m_delta"] = group["volume"].diff(3).div(group["volume"].shift(3).replace(0, float("nan")))
        group["vol_5m_delta"] = group["volume"].diff(5).div(group["volume"].shift(5).replace(0, float("nan")))

        # 价格相对于 VWAP
        group["price_vs_vwap"] = (group["price"] - group["vwap"]) / group["vwap"].replace(0, float("nan"))

        results.append(group)

    return pd.concat(results, ignore_index=True) if results else df


# ── 分钟摆动点 ─────────────────────────────────────────────────────


def find_named_pivots(
    minute_df: pd.DataFrame,
    *,
    time_col: str = "time",
    price_col: str = "price",
    window: int = 3,
) -> pd.DataFrame:
    """从分钟序列中识别摆动高/低点.

    摆动高点: 价格高于前后各 window 根 K 线
    摆动低点: 价格低于前后各 window 根 K 线

    返回 DataFrame 包含: code, time, price, pivot_type (high/low), sequence
    """
    if minute_df.empty:
        return pd.DataFrame(columns=["code", "time", "price", "pivot_type", "sequence"])

    df = minute_df.copy()
    for col in (time_col, price_col):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") if col == price_col else pd.to_datetime(df[col], errors="coerce")

    pivots: list[dict[str, object]] = []
    for code, group in df.groupby("code"):
        group = group.sort_values(time_col).reset_index(drop=True)
        prices = group[price_col].values
        times = group[time_col].values
        if len(prices) < 2 * window + 1:
            continue

        for i in range(window, len(prices) - window):
            left = prices[i - window : i]
            right = prices[i + 1 : i + window + 1]
            if prices[i] > max(left) and prices[i] > max(right):
                pivots.append({
                    "code": code,
                    "time": str(times[i]),
                    "price": float(prices[i]),
                    "pivot_type": "high",
                    "sequence": int(i),
                })
            elif prices[i] < min(left) and prices[i] < min(right):
                pivots.append({
                    "code": code,
                    "time": str(times[i]),
                    "price": float(prices[i]),
                    "pivot_type": "low",
                    "sequence": int(i),
                })

    return pd.DataFrame(pivots) if pivots else pd.DataFrame(columns=["code", "time", "price", "pivot_type", "sequence"])


# ── 开盘代理价 ─────────────────────────────────────────────────────


def extract_open_proxy_price(
    minute_df: pd.DataFrame,
    *,
    time_col: str = "time",
    price_col: str = "price",
    open_minute: str = "09:30",
) -> pd.Series:
    """提取每只股票的开盘代理价.

    优先取开盘集合竞价成交价，无则取第一笔分钟成交价（09:30）.
    返回以 code 为索引的 Series.
    """
    if minute_df.empty:
        return pd.Series(dtype="float64")

    df = minute_df.copy()
    if time_col not in df.columns or price_col not in df.columns:
        return pd.Series(dtype="float64")

    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")

    # 筛选 09:30 的分钟数据（开盘第一分钟）
    first_minute = df.loc[
        df[time_col].dt.strftime("%H:%M") == open_minute,
        ["code", price_col],
    ].dropna(subset=[price_col])

    if first_minute.empty:
        return pd.Series(dtype="float64")

    # 取第一笔成交价
    result = first_minute.groupby("code")[price_col].first()
    return result


# ── 午后摆动低点 ───────────────────────────────────────────────────


def find_afternoon_swing_low(
    pivots_df: pd.DataFrame,
    *,
    afternoon_start: str = "13:00",
) -> pd.DataFrame:
    """从命名摆动点中筛选 13:00 之后的最近摆动低点.

    输入: find_named_pivots 的输出
    返回: 每只股票在 13:00 后最近的 swing low 记录
    """
    if pivots_df.empty:
        return pd.DataFrame(columns=pivots_df.columns)

    df = pivots_df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")

    # 筛选午后低点
    afternoon = df.loc[
        (df["pivot_type"] == "low") &
        (df["time"].dt.time >= pd.Timestamp(afternoon_start).time())
    ].copy()

    if afternoon.empty:
        return pd.DataFrame(columns=pivots_df.columns)

    # 每只股票取最近（时间最大）的午后低点
    afternoon = afternoon.sort_values("time")
    result = afternoon.groupby("code").last().reset_index()
    return result


# ── 分钟客观事实字段 ─────────────────────────────────────────────────


def compute_minute_facts(
    minute_df: pd.DataFrame,
    snapshot_df: pd.DataFrame,
    *,
    time_col: str = "time",
    price_col: str = "price",
    amount_col: str = "amount",
    afternoon_start: str = "13:00",
) -> pd.DataFrame:
    """从分钟序列提取客观事实字段，写入快照 DataFrame.

    纯事实记录，不做策略判断:
      - afternoon_pivot: 13:00 后最近摆动低点价格
      - distance_to_day_high_pct: (day_high - price) / day_high * 100
      - vol_last_3m: 最近 3 分钟成交额
      - vol_last_5m: 最近 5 分钟成交额
      - vol_change_3m_pct: 近 3 分钟成交额变化率
      - is_limit_touched: 是否触及涨跌停
      - last_tradable_time: 最后正常可交易时间
    """
    result = snapshot_df.copy()

    fact_cols = [
        "afternoon_pivot", "distance_to_day_high_pct",
        "vol_last_3m", "vol_last_5m", "vol_change_3m_pct",
        "is_limit_touched", "last_tradable_time",
    ]
    for col in fact_cols:
        if col not in result.columns:
            result[col] = float("nan")

    if minute_df.empty:
        return result

    for col in (price_col, "high", "low", "pre_close"):
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    # 1. distance_to_day_high_pct
    if "high" in result.columns and price_col in result.columns:
        day_high = result["high"]
        price_series = result[price_col]
        result["distance_to_day_high_pct"] = (
            (day_high - price_series) / day_high.replace(0, float("nan")) * 100
        ).round(4)

    # 2. 午后摆动低点
    pivots = find_named_pivots(minute_df, time_col=time_col, price_col=price_col)
    if not pivots.empty:
        afternoon_lows = find_afternoon_swing_low(pivots, afternoon_start=afternoon_start)
        if not afternoon_lows.empty:
            pivot_map = dict(zip(afternoon_lows["code"], afternoon_lows["price"]))
            result["afternoon_pivot"] = result["code"].map(pivot_map)

    # 3. 近 N 分钟成交额与变化率
    if time_col in minute_df.columns and amount_col in minute_df.columns and "code" in minute_df.columns:
        minute_sorted = minute_df.sort_values([time_col])
        for code, group in minute_sorted.groupby("code"):
            group = group.sort_values(time_col).reset_index(drop=True)
            amt = pd.to_numeric(group[amount_col], errors="coerce").fillna(0.0)
            mask = result["code"] == code
            if len(amt) >= 3:
                result.loc[mask, "vol_last_3m"] = float(amt.iloc[-3:].sum())
                if len(amt) >= 6:
                    prev_3m = float(amt.iloc[-6:-3].sum())
                    if prev_3m > 0:
                        result.loc[mask, "vol_change_3m_pct"] = round(
                            (float(amt.iloc[-3:].sum()) - prev_3m) / prev_3m * 100, 2
                        )
            if len(amt) >= 5:
                result.loc[mask, "vol_last_5m"] = float(amt.iloc[-5:].sum())

    # 4. 涨跌停触及（盘中任一分钟触及，非当前快照状态）
    if "pre_close" in result.columns:
        result["is_limit_touched"] = False
        # 优先使用 minute_df 中的 high/low 判断盘中是否曾触及
        if "high" in minute_df.columns and "low" in minute_df.columns and "code" in minute_df.columns:
            for code in result["code"].unique():
                mask = result["code"] == code
                pc = pd.to_numeric(result.loc[mask, "pre_close"], errors="coerce").values
                if len(pc) == 0 or pd.isna(pc[0]) or pc[0] <= 0:
                    continue
                limit_up = float(pc[0]) * 1.10
                limit_down = float(pc[0]) * 0.90
                code_minute = minute_df[minute_df["code"] == code]
                if code_minute.empty:
                    continue
                code_high = pd.to_numeric(code_minute["high"], errors="coerce")
                code_low = pd.to_numeric(code_minute["low"], errors="coerce")
                touched = bool(
                    (code_high >= limit_up).any() or (code_low <= limit_down).any()
                )
                if touched:
                    result.loc[mask, "is_limit_touched"] = True
        # 回退：用当前快照涨跌停状态
        elif "is_limit_up" in result.columns and "is_limit_down" in result.columns:
            result["is_limit_touched"] = (
                result["is_limit_up"].fillna(False) | result["is_limit_down"].fillna(False)
            )

    # 5. 最后正常可交易时间
    if "code" in minute_df.columns and time_col in minute_df.columns:
        last_time_map = minute_df.groupby("code")[time_col].max()
        result["last_tradable_time"] = result["code"].map(last_time_map)

    return result
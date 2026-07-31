"""全市场分钟快照 —— 盘中连续采集与盘后分钟序列构建.

基于每分钟全市场快照拼成 intraday_1m.parquet，支持:
  - 全日 VWAP (基于累计量额)
  - 锚定 VWAP (从指定时刻起的 VWAP)
  - 近 N 分钟量能变化
  - 分钟数据质量审计
  - 分钟摆动高/低点 (named pivot)
  - 开盘代理价 (开盘集合竞价或第一笔成交)
  - 午后摆动低点 (13:00 后最近摆动低点)

用于盘中连续性确认（如连续3分钟站稳VWAP）和尾盘确认.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import pandas as pd


# ── 分钟数据质量审计 ─────────────────────────────────────────────────


def audit_minute_sequence(
    frame: pd.DataFrame,
    *,
    expected_start: str = "09:30",
    expected_end: str = "15:00",
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """审计分钟序列的质量.

    检查:
      - 覆盖时间范围
      - 缺失分钟
      - 时间连续性断点
      - 每只股票的分钟覆盖率
    """
    audit: dict[str, object] = {
        "generated_at": (observed_at or datetime.now().astimezone()).isoformat(),
        "expected_start": expected_start,
        "expected_end": expected_end,
    }

    if frame.empty:
        audit["error"] = "empty frame"
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

    audit["total_expected_minutes"] = _expected_minutes_count(expected_start, expected_end)
    audit["actual_minutes"] = int(valid_times.nunique())
    audit["latest_minute_time"] = str(valid_times.max()) if not valid_times.empty else None
    audit["earliest_minute_time"] = str(valid_times.min()) if not valid_times.empty else None

    # 缺失分钟
    all_minutes = pd.date_range(
        valid_times.min().floor("min"),
        valid_times.max().floor("min"),
        freq="min",
    )
    present_minutes = set(valid_times.dt.floor("min"))
    missing = sorted(set(all_minutes) - present_minutes)
    audit["missing_minutes"] = [str(m) for m in missing[:20]]
    audit["missing_minute_count"] = len(missing)

    # 时间连续性断点 (>2分钟间隔)
    if len(valid_times) > 1:
        sorted_times = valid_times.sort_values().reset_index(drop=True)
        gaps = sorted_times.diff().dropna()
        breaks = gaps[gaps > timedelta(minutes=2)]
        audit["time_continuity_breaks"] = [
            {"after": str(sorted_times.iloc[i]), "gap_seconds": gap.total_seconds()}
            for i, gap in breaks.items()
        ][:20]
        audit["continuity_break_count"] = len(breaks)

    # 每只股票的分钟覆盖率
    if "code" in frame.columns:
        code_time_counts = frame.groupby("code")[time_col].nunique()
        total_minutes = audit["actual_minutes"]
        coverage = (code_time_counts / max(total_minutes, 1)).describe().to_dict()
        audit["code_coverage"] = {
            "mean": round(coverage.get("mean", 0), 4),
            "min": round(coverage.get("min", 0), 4),
            "max": round(coverage.get("max", 0), 4),
            "codes_full": int((code_time_counts >= total_minutes * 0.95).sum()),
            "codes_partial": int(((code_time_counts < total_minutes * 0.95) & (code_time_counts > 0)).sum()),
            "codes_none": int((code_time_counts == 0).sum()),
        }

    return audit


def _expected_minutes_count(start: str, end: str) -> int:
    """计算预期交易分钟数 (09:30-11:30, 13:00-15:00 = 240)."""
    try:
        s_h, s_m = map(int, start.split(":"))
        e_h, e_m = map(int, end.split(":"))
        total = (e_h * 60 + e_m) - (s_h * 60 + s_m)
        # 减去午休 11:30-13:00 (90分钟)
        if s_h < 12 and e_h >= 13:
            total -= 90
        return max(total, 0)
    except (ValueError, AttributeError):
        return 240


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
"""中性技术指标 —— 纯数学计算，不含方向标签或信号分。

输出指标包括：MA(5/10/11/20/23/60/120/250)、RSI(14)、MACD(DIF/DEA/Hist)、ATR(14)、
BOLL(上轨/中轨/下轨/位置)、RPS(20)、多周期回报、上/下影比率、重复上影线标签、过热标签、
动量增量(momentum_delta_1/3)、动量改善(momentum_improving)。
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd


_OUTPUT_KEYS = (
    "ma5",
    "ma10",
    "ma11",
    "ma20",
    "ma23",
    "ma60",
    "ma120",
    "ma250",
    "rsi14",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "atr14",
    "atr14_pct",
    "boll_upper",
    "boll_middle",
    "boll_lower",
    "boll_position",
    "return_5d",
    "return_10d",
    "return_20d",
    "upper_shadow_ratio",
    "lower_shadow_ratio",
    "repeated_upper_shadow",
    "overheated",
    "momentum_delta_1",
    "momentum_delta_3",
    "momentum_improving",
    "input_rows",
    "first_date",
    "last_date",
    "calculated_at",
)


def compute_daily_indicators(
    frame: pd.DataFrame,
    *,
    calculated_at: datetime | None = None,
    trading_date: str | None = None,
) -> dict[str, object]:
    """基于日线 OHLCV 数据计算中性指标。

    提供 *trading_date*（ISO 日期字符串）时，排除该日期及之后的数据，
    确保指标仅反映已完成的交易日。
    """
    rows = int(len(frame))
    result: dict[str, object] = {key: None for key in _OUTPUT_KEYS}
    result["input_rows"] = rows
    result["calculated_at"] = _iso_datetime(calculated_at)

    if rows == 0:
        return result

    df = frame.copy()
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
        # 截断到已完成交易日（排除当日未完成数据）
        if trading_date:
            cutoff = pd.Timestamp(trading_date)
            keep_mask = dates < cutoff
            if keep_mask.sum() < rows:
                result["truncated_from"] = rows
                result["truncated_to"] = int(keep_mask.sum())
            df = df.loc[keep_mask].copy()
            dates = dates.loc[keep_mask]
        # 最多保留最近 250 个完整交易日以确保指标稳定
        if len(df) > 250:
            df = df.tail(250).reset_index(drop=True)
            dates = dates.tail(250).reset_index(drop=True)
        valid_dates = dates.dropna()
        if not valid_dates.empty:
            result["first_date"] = valid_dates.min().date().isoformat()
            result["last_date"] = valid_dates.max().date().isoformat()
        df = df.assign(_indicator_date=dates).sort_values("_indicator_date")

    close = _numeric_series(df, "close")
    if close.empty:
        return result

    for period in (5, 10, 11, 20, 23, 60, 120, 250):
        result[f"ma{period}"] = _last_value(close.rolling(period, min_periods=period).mean())

    result["rsi14"] = _rsi_wilder(close, 14)
    dif, dea, hist, hist_series = _macd_with_series(close)
    result["macd_dif"] = dif
    result["macd_dea"] = dea
    result["macd_hist"] = hist

    # ── 动量增量 ──────────────────────────────────────────────────
    result["momentum_delta_1"] = _momentum_delta(hist_series, 1)
    result["momentum_delta_3"] = _momentum_delta(hist_series, 3)
    result["momentum_improving"] = _momentum_improving(hist_series)

    atr = _atr_wilder(df, 14)
    result["atr14"] = atr
    latest_close = float(close.iloc[-1])
    result["atr14_pct"] = None if atr is None or latest_close == 0 else atr / latest_close * 100

    # ── BOLL(20,2) ──────────────────────────────────────────────────
    boll_result = _bollinger(close, period=20, std=2)
    result["boll_upper"] = boll_result["upper"]
    result["boll_middle"] = boll_result["middle"]
    result["boll_lower"] = boll_result["lower"]
    result["boll_position"] = boll_result["position"]

    # ── 多周期回报 ──────────────────────────────────────────────────
    result["return_5d"] = _return_n(close, 5)
    result["return_10d"] = _return_n(close, 10)
    result["return_20d"] = _return_n(close, 20)

    # ── 上/下影比率 ─────────────────────────────────────────────────
    high = _numeric_series(df, "high")
    low = _numeric_series(df, "low")
    open_ = _numeric_series(df, "open")
    result["upper_shadow_ratio"] = _shadow_ratio(high, low, open_, close, "upper")
    result["lower_shadow_ratio"] = _shadow_ratio(high, low, open_, close, "lower")

    # ── 标签 ────────────────────────────────────────────────────────
    result["repeated_upper_shadow"] = _repeated_upper_shadow(high, low, open_, close)
    result["overheated"] = _check_overheated(
        result["rsi14"], result["boll_position"]
    )

    return result


def compute_vwap(frame: pd.DataFrame) -> float | None:
    """计算成交量加权平均价格（VWAP），不猜测成交量单位."""
    if frame.empty or not {"volume", "amount"}.issubset(frame.columns):
        return None
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    valid = volume.notna() & amount.notna()
    total_volume = float(volume[valid].sum())
    if total_volume == 0:
        return None
    return float(amount[valid].sum()) / total_volume


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").dropna().reset_index(drop=True)


def _last_value(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def _rsi_wilder(close: pd.Series, period: int) -> float | None:
    if len(close) <= period:
        return None
    delta = close.diff().dropna().reset_index(drop=True)
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = float(gains.iloc[:period].mean())
    avg_loss = float(losses.iloc[:period].mean())
    for gain, loss in zip(gains.iloc[period:], losses.iloc[period:]):
        avg_gain = (avg_gain * (period - 1) + float(gain)) / period
        avg_loss = (avg_loss * (period - 1) + float(loss)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _macd_with_series(
    close: pd.Series,
) -> tuple[float | None, float | None, float | None, pd.Series]:
    """返回 (last_dif, last_dea, last_hist, hist_series)."""
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    last_dif = _last_value(dif)
    last_dea = _last_value(dea)
    last_hist_val = None if last_dif is None or last_dea is None else (last_dif - last_dea) * 2
    hist_series = (dif - dea) * 2
    return last_dif, last_dea, last_hist_val, hist_series


def _macd(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    """兼容旧接口."""
    dif, dea, hist, _ = _macd_with_series(close)
    return dif, dea, hist


def _momentum_delta(hist_series: pd.Series, lag: int) -> float | None:
    """MACD 柱在最近一根 K 线与 lag 根前 K 线的差值.

    momentum_delta_n = hist[t] - hist[t-n]
    """
    if hist_series.empty or len(hist_series) <= lag:
        return None
    current = float(hist_series.iloc[-1])
    prev = float(hist_series.iloc[-(lag + 1)])
    if pd.isna(current) or pd.isna(prev):
        return None
    return current - prev


def _momentum_improving(hist_series: pd.Series) -> bool | None:
    """动量改善: momentum_delta_1 > 0 且 momentum_delta_3 > 0."""
    d1 = _momentum_delta(hist_series, 1)
    d3 = _momentum_delta(hist_series, 3)
    if d1 is None or d3 is None:
        return None
    return d1 > 0 and d3 > 0


def _atr_wilder(frame: pd.DataFrame, period: int) -> float | None:
    required = {"high", "low", "close"}
    if not required.issubset(frame.columns):
        return None
    ohlc = frame.loc[:, sorted(required)].apply(pd.to_numeric, errors="coerce").dropna()
    if len(ohlc) < period:
        return None
    previous_close = ohlc["close"].shift(1)
    true_range = pd.concat(
        [ohlc["high"] - ohlc["low"], (ohlc["high"] - previous_close).abs(), (ohlc["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    true_range.iloc[0] = float(ohlc["high"].iloc[0] - ohlc["low"].iloc[0])
    values = true_range.to_numpy(dtype=float)
    atr = float(values[:period].mean())
    for value in values[period:]:
        atr = (atr * (period - 1) + float(value)) / period
    return atr


def _iso_datetime(value: datetime | None) -> str:
    current = value if value is not None else datetime.now().astimezone()
    return current.isoformat()


# ── BOLL 布林带 ─────────────────────────────────────────────────────


def _bollinger(close: pd.Series, period: int = 20, std: float = 2.0) -> dict[str, float | None]:
    """计算 BOLL(period, std) 上轨/中轨/下轨及位置."""
    if len(close) < period:
        return {"upper": None, "middle": None, "lower": None, "position": None}
    middle = float(close.rolling(period, min_periods=period).mean().iloc[-1])
    if pd.isna(middle):
        return {"upper": None, "middle": None, "lower": None, "position": None}
    sigma = float(close.rolling(period, min_periods=period).std().iloc[-1])
    upper = middle + std * sigma
    lower = middle - std * sigma
    latest = float(close.iloc[-1])
    if upper == lower:
        return {"upper": upper, "middle": middle, "lower": lower, "position": 0.5}
    position = (latest - lower) / (upper - lower)
    return {"upper": upper, "middle": middle, "lower": lower, "position": position}


# ── 多周期回报 ──────────────────────────────────────────────────────


def _return_n(close: pd.Series, n: int) -> float | None:
    """计算 N 日回报率 (close / close.shift(n) - 1)."""
    if len(close) <= n:
        return None
    prev = float(close.iloc[-(n + 1)])
    latest = float(close.iloc[-1])
    if prev == 0:
        return None
    return (latest - prev) / prev


# ── 影线比率 ────────────────────────────────────────────────────────


def _shadow_ratio(
    high: pd.Series,
    low: pd.Series,
    open_: pd.Series,
    close: pd.Series,
    which: str,
) -> float | None:
    """计算上影或下影比率（最近一根 K 线）.

    upper_shadow_ratio = (high - max(open, close)) / (high - low)
    lower_shadow_ratio = (min(open, close) - low) / (high - low)
    """
    if len(high) == 0 or len(low) == 0 or len(open_) == 0 or len(close) == 0:
        return None
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    o = float(open_.iloc[-1])
    c = float(close.iloc[-1])
    body_high = max(o, c)
    body_low = min(o, c)
    hl_range = h - l
    if hl_range <= 0 or pd.isna(hl_range):
        return None
    if which == "upper":
        return (h - body_high) / hl_range
    return (body_low - l) / hl_range


def _repeated_upper_shadow(
    high: pd.Series,
    low: pd.Series,
    open_: pd.Series,
    close: pd.Series,
    threshold: float = 0.6,
    window: int = 5,
    min_count: int = 3,
) -> bool | None:
    """检查最近 window 天内是否有 >= min_count 天的上影比率 > threshold."""
    if len(high) < window or len(low) < window or len(open_) < window or len(close) < window:
        return None
    ratios = []
    for i in range(-window, 0):
        h = float(high.iloc[i])
        l = float(low.iloc[i])
        o = float(open_.iloc[i])
        c = float(close.iloc[i])
        hl_range = h - l
        if hl_range <= 0 or pd.isna(hl_range):
            continue
        body_high = max(o, c)
        ratios.append((h - body_high) / hl_range)
    if len(ratios) < window:
        return None
    return sum(1 for r in ratios if r > threshold) >= min_count


def _check_overheated(rsi: float | None, boll_position: float | None) -> bool | None:
    """过热标签: RSI14 > 70 且 boll_position > 0.8."""
    if rsi is None or boll_position is None:
        return None
    return rsi > 70 and boll_position > 0.8


# ── RPS20 全市场排序 ────────────────────────────────────────────────


def compute_rps20(indicators_df: pd.DataFrame) -> pd.Series:
    """基于 return_20d 计算全市场 RPS20 排名 (0-100).

    需要 indicators_df 包含 code 和 return_20d 列.
    返回以 code 为索引的 Series.
    """
    if indicators_df.empty or "return_20d" not in indicators_df.columns:
        return pd.Series(dtype=float)
    valid = indicators_df.loc[indicators_df["return_20d"].notna(), ["code", "return_20d"]].copy()
    if valid.empty:
        return pd.Series(dtype=float)
    valid["rps20"] = valid["return_20d"].rank(pct=True) * 100
    return valid.set_index("code")["rps20"]

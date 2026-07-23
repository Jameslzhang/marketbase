"""Neutral, presentation-independent technical indicators."""

from __future__ import annotations

from datetime import datetime

import pandas as pd


_OUTPUT_KEYS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ma250",
    "rsi14",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "atr14",
    "atr14_pct",
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
    """Compute neutral indicators from daily OHLCV data.

    When *trading_date* is provided (ISO date string), rows on or after that
    date are excluded so indicators only reflect complete trading days.
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
        valid_dates = dates.dropna()
        if not valid_dates.empty:
            result["first_date"] = valid_dates.min().date().isoformat()
            result["last_date"] = valid_dates.max().date().isoformat()
        # Truncate to complete trading days only
        if trading_date:
            cutoff = pd.Timestamp(trading_date)
            keep_mask = dates < cutoff
            if keep_mask.sum() < rows:
                result["truncated_from"] = rows
                result["truncated_to"] = int(keep_mask.sum())
            df = df.loc[keep_mask].copy()
            dates = dates.loc[keep_mask]
        df = df.assign(_indicator_date=dates).sort_values("_indicator_date")

    close = _numeric_series(df, "close")
    if close.empty:
        return result

    for period in (5, 10, 20, 60, 120, 250):
        result[f"ma{period}"] = _last_value(close.rolling(period, min_periods=period).mean())

    result["rsi14"] = _rsi_wilder(close, 14)
    dif, dea, hist = _macd(close)
    result["macd_dif"] = dif
    result["macd_dea"] = dea
    result["macd_hist"] = hist

    atr = _atr_wilder(df, 14)
    result["atr14"] = atr
    latest_close = float(close.iloc[-1])
    result["atr14_pct"] = None if atr is None or latest_close == 0 else atr / latest_close * 100
    return result


def compute_vwap(frame: pd.DataFrame) -> float | None:
    """Compute volume-weighted average price without guessing volume units."""
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


def _macd(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    last_dif = _last_value(dif)
    last_dea = _last_value(dea)
    hist = None if last_dif is None or last_dea is None else (last_dif - last_dea) * 2
    return last_dif, last_dea, hist


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

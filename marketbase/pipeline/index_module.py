"""指数数据采集 —— 从 local_workflow.py 提取.

采集上证指数、沪深300、中证1000、创业板指、科创50的日线数据，
盘中阶段优先使用 EastMoney 实时指数数据.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import math
from pathlib import Path
from typing import Any, cast

import pandas as pd

_INDEX_CODES: dict[str, str] = {
    "000001": "上证指数",
    "000300": "沪深300",
    "000852": "中证1000",
    "399006": "创业板指",
    "000688": "科创50",
}

_INDEX_DATA_COLUMNS = [
    "code", "name", "date", "open", "high", "low", "close",
    "pre_close", "pct_chg", "volume", "amount",
    "ma5", "ma10", "ma20", "ma60",
    "return_5d", "return_10d", "return_20d",
    "intraday_price", "intraday_change_pct", "day_high", "day_low",
    "intraday_amount", "quote_time",
]


def _run_index_collection(
    _frame: pd.DataFrame,
    cache_root: Path,
    _observed_at: datetime,
    emit: Callable[[str], None],
) -> tuple[pd.DataFrame, bool]:
    """采集指数日线数据并计算基础指标.
    
    盘中阶段优先采集 EastMoney 实时指数数据，
    收盘后使用日线历史数据.

    Returns: (DataFrame, index_intraday_ready)
    """
    from marketbase.daily import fetch_index_daily_history

    emit("index collection: starting")
    rows: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
    index_intraday_ready = False

    def _safe_float(v: Any, default: float = 0.0) -> float:  # pyright: ignore[reportExplicitAny]
        if v is None:
            return default
        if isinstance(v, float) and math.isnan(v):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ── 尝试获取实时指数数据 ──
    intraday_indices = _fetch_index_intraday(emit)
    if intraday_indices:
        index_intraday_ready = True

    for code, name in _INDEX_CODES.items():
        try:
            # 盘中优先使用实时数据
            if code in intraday_indices:
                intraday_row = intraday_indices[code]
                # 仍需日线数据计算 MA 和 returns
                raw = fetch_index_daily_history(code, lookback_days=250, cache_dir=cache_root)
                if len(raw) >= 5:
                    raw = raw.sort_values("date").reset_index(drop=True)
                    close = cast(pd.Series, raw["close"]).astype(float)
                    pre_close = cast(pd.Series, raw["pre_close"]).astype(float) if "pre_close" in raw.columns else close.shift(1)

                    def _ma(col: pd.Series, period: int) -> float | None:
                        if len(col) < period:
                            return None
                        return float(col.iloc[-period:].mean())

                    def _return(col: pd.Series, period: int) -> float | None:
                        if len(col) < period + 1:
                            return None
                        return float((col.iloc[-1] - col.iloc[-(period + 1)]) / col.iloc[-(period + 1)])

                    rows.append({
                        "code": code,
                        "name": name,
                        "date": intraday_row.get("date", ""),
                        "open": _safe_float(intraday_row.get("open")),
                        "high": _safe_float(intraday_row.get("high")),
                        "low": _safe_float(intraday_row.get("low")),
                        "close": _safe_float(intraday_row.get("price")),
                        "pre_close": _safe_float(intraday_row.get("pre_close")),
                        "pct_chg": _safe_float(intraday_row.get("change_pct")),
                        "volume": _safe_float(intraday_row.get("volume")),
                        "amount": _safe_float(intraday_row.get("amount")),
                        "ma5": _ma(close, 5),
                        "ma10": _ma(close, 10),
                        "ma20": _ma(close, 20),
                        "ma60": _ma(close, min(60, len(close))),
                        "return_5d": _return(close, 5),
                        "return_10d": _return(close, 10),
                        "return_20d": _return(close, 20),
                        # 盘中实时字段
                        "intraday_price": _safe_float(intraday_row.get("price")),
                        "intraday_change_pct": _safe_float(intraday_row.get("change_pct")),
                        "day_high": _safe_float(intraday_row.get("high")),
                        "day_low": _safe_float(intraday_row.get("low")),
                        "intraday_amount": _safe_float(intraday_row.get("amount")),
                        "quote_time": intraday_row.get("quote_time", ""),
                    })
                continue

            # 收盘后或无实时数据：使用日线历史
            raw = fetch_index_daily_history(code, lookback_days=250, cache_dir=cache_root)
            if len(raw) < 5:
                emit(f"index collection: {code} ({name}) insufficient data")
                continue

            raw = raw.sort_values("date").reset_index(drop=True)
            close = cast(pd.Series, raw["close"]).astype(float)
            pre_close = cast(pd.Series, raw["pre_close"]).astype(float) if "pre_close" in raw.columns else close.shift(1)
            latest = raw.iloc[-1]

            def _ma(col: pd.Series, period: int) -> float | None:
                if len(col) < period:
                    return None
                return float(col.iloc[-period:].mean())

            def _return(col: pd.Series, period: int) -> float | None:
                if len(col) < period + 1:
                    return None
                return float((col.iloc[-1] - col.iloc[-(period + 1)]) / col.iloc[-(period + 1)])

            rows.append({
                "code": code,
                "name": name,
                "date": str(latest.get("date", "")),
                "open": _safe_float(latest.get("open")),
                "high": _safe_float(latest.get("high")),
                "low": _safe_float(latest.get("low")),
                "close": _safe_float(latest.get("close")),
                "pre_close": float(pre_close.iloc[-1]) if not pd.isna(pre_close.iloc[-1]) else None,
                "pct_chg": _safe_float(latest.get("pct_chg"), 0.0) if "pct_chg" in raw.columns else None,
                "volume": _safe_float(latest.get("volume")),
                "amount": _safe_float(latest.get("amount")),
                "ma5": _ma(close, 5),
                "ma10": _ma(close, 10),
                "ma20": _ma(close, 20),
                "ma60": _ma(close, min(60, len(close))),
                "return_5d": _return(close, 5),
                "return_10d": _return(close, 10),
                "return_20d": _return(close, 20),
                # 盘中实时字段（盘后从日线兜底填充）
                "intraday_price": _safe_float(latest.get("close")),
                "intraday_change_pct": _safe_float(latest.get("pct_chg"), 0.0) if "pct_chg" in raw.columns else None,
                "day_high": _safe_float(latest.get("high")),
                "day_low": _safe_float(latest.get("low")),
                "intraday_amount": _safe_float(latest.get("amount")),
                "quote_time": str(latest.get("date", "")),
            })
        except Exception as exc:
            emit(f"index collection: {code} ({name}) error: {exc}")

    emit(f"index collection: {len(rows)} indices (intraday_ready={index_intraday_ready})")
    if not rows:
        return pd.DataFrame(columns=_INDEX_DATA_COLUMNS), index_intraday_ready
    return pd.DataFrame(rows), index_intraday_ready


def _fetch_index_intraday(
    emit: Callable[[str], None],
) -> dict[str, dict[str, object]]:
    """从 EastMoney 实时接口获取四大指数的盘中数据.
    
    返回 dict: code -> {price, pre_close, open, high, low, change_pct, 
                        volume, amount, date, quote_time, day_high, day_low}
    """
    from marketbase.snapshot import _eastmoney_get
    from datetime import datetime, timezone, timedelta

    # EastMoney secids 格式: 1=沪市, 0=深市
    index_secids = {
        "000001": "1.000001",
        "000300": "1.000300",
        "000852": "1.000852",
        "399006": "0.399006",
        "000688": "1.000688",
    }

    secids_str = ",".join(index_secids.values())
    try:
        resp = _eastmoney_get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18,f124",
                "secids": secids_str,
            },
            timeout=10,
        )
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
    except Exception as exc:
        emit(f"index intraday fetch failed: {exc}")
        return {}

    tz_cn = timezone(timedelta(hours=8))
    result: dict[str, dict[str, object]] = {}
    for item in items:
        code = item.get("f12", "")
        price = float(item.get("f2", 0)) if item.get("f2") else 0.0
        if price <= 0:
            continue
        pre_close = float(item.get("f18", 0)) if item.get("f18") else 0.0
        open_price = float(item.get("f17", 0)) if item.get("f17") else 0.0
        high = float(item.get("f15", 0)) if item.get("f15") else 0.0
        low = float(item.get("f16", 0)) if item.get("f16") else 0.0

        # 解析行情时间戳 (f124: Unix timestamp)
        ts = item.get("f124")
        quote_time = ""
        if ts:
            try:
                quote_time = datetime.fromtimestamp(int(ts), tz=tz_cn).strftime("%H:%M:%S")
            except (ValueError, OSError):
                pass

        result[code] = {
            "name": item.get("f14", ""),
            "price": price,
            "pre_close": pre_close,
            "open": open_price,
            "high": high,
            "low": low,
            "change_pct": float(item.get("f3", 0)) if item.get("f3") else 0.0,
            "volume": float(item.get("f5", 0)) if item.get("f5") else 0.0,
            "amount": float(item.get("f6", 0)) if item.get("f6") else 0.0,
            "date": datetime.fromtimestamp(int(ts), tz=tz_cn).strftime("%Y-%m-%d") if ts else "",
            "quote_time": quote_time,
            "day_high": high,
            "day_low": low,
        }

    if result:
        emit(f"index intraday: {len(result)} indices from EastMoney real-time")
    return result
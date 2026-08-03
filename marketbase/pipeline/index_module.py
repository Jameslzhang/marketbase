"""指数数据采集 —— 从 local_workflow.py 提取.

采集沪深300、中证1000、创业板指、科创50的日线数据，
盘中阶段优先使用 Sina 实时指数数据.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import math
from pathlib import Path
from typing import Any, cast

import pandas as pd

_INDEX_CODES: dict[str, str] = {
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


def _parse_index_quote_time(fields: list[str]) -> str:
    """Parse quote_time from Sina index API fields.

    Sina index API returns fields as comma-separated values:
      - fields[30] = date (YYYYMMDD, 8 digits)
      - fields[31] = time (HHMMSS, 6 digits)
    Also handles legacy 14-digit combined format (YYYYMMDDHHMMSS) in field 30.
    """
    if len(fields) <= 30:
        return ""
    raw_date = fields[30].strip() if fields[30] else ""
    raw_time = fields[31].strip() if len(fields) > 31 and fields[31] else ""

    if not raw_date:
        return ""

    # 14-digit combined format: YYYYMMDDHHMMSS
    if len(raw_date) == 14 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}T{raw_date[8:10]}:{raw_date[10:12]}:{raw_date[12:14]}"

    # 8-digit date + 6-digit time: YYYYMMDD + HHMMSS
    if len(raw_date) == 8 and raw_date.isdigit() and len(raw_time) == 6 and raw_time.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}T{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}"

    # 8-digit date only, no time
    if len(raw_date) == 8 and raw_date.isdigit():
        return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}T00:00:00"

    return raw_date


def _run_index_collection(
    _frame: pd.DataFrame,
    cache_root: Path,
    _observed_at: datetime,
    emit: Callable[[str], None],
) -> tuple[pd.DataFrame, bool]:
    """采集指数日线数据并计算基础指标.
    
    盘中阶段优先采集 Sina 实时指数数据，
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
                # 盘中实时字段（日线兜底，标记不可用）
                "intraday_price": float("nan"),
                "intraday_change_pct": float("nan"),
                "day_high": float("nan"),
                "day_low": float("nan"),
                "intraday_amount": float("nan"),
                "quote_time": "",
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
    """从 Sina 实时接口获取四大指数的盘中数据.
    
    返回 dict: code -> {price, pre_close, open, high, low, change_pct, 
                        volume, amount, date, quote_time, day_high, day_low}
    """
    import requests

    # Sina 指数代码映射（标准格式，非 s_ 简版）
    sina_symbols = {
        "000300": "sh000300",
        "000852": "sh000852",
        "399006": "sz399006",
        "000688": "sh000688",
    }
    code_from_symbol = {v: k for k, v in sina_symbols.items()}

    symbols_str = ",".join(sina_symbols.values())
    try:
        resp = requests.get(
            "https://hq.sinajs.cn/list=" + symbols_str,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.content.decode("gb18030", errors="ignore")
    except Exception as exc:
        emit(f"index intraday fetch failed: {exc}")
        return {}

    import re
    result: dict[str, dict[str, object]] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'var hq_str_([shz]\d{6})="([^"]*)"', line)
        if not match:
            continue
        symbol, body = match.groups()
        code = code_from_symbol.get(symbol)
        if not code:
            continue
        fields = body.split(",")
        if len(fields) < 6:
            continue
        try:
            # 标准格式字段: 0=name, 1=open, 2=pre_close, 3=price, 4=high, 5=low
            price = float(fields[3]) if fields[3] else 0.0
            if price <= 0:
                continue
            pre_close = float(fields[2]) if len(fields) > 2 and fields[2] else 0.0
            result[code] = {
                "name": fields[0].strip(),
                "price": price,
                "pre_close": pre_close,
                "open": float(fields[1]) if len(fields) > 1 and fields[1] else 0.0,
                "high": float(fields[4]) if len(fields) > 4 and fields[4] else 0.0,
                "low": float(fields[5]) if len(fields) > 5 and fields[5] else 0.0,
                "change_pct": round((price - pre_close) / pre_close * 100, 2) if pre_close else 0.0,
                "volume": float(fields[8]) if len(fields) > 8 and fields[8] else 0.0,
                "amount": float(fields[9]) if len(fields) > 9 and fields[9] else 0.0,
                "date": fields[30] if len(fields) > 30 else "",
                "quote_time": _parse_index_quote_time(fields),
                "day_high": float(fields[4]) if len(fields) > 4 and fields[4] else 0.0,
                "day_low": float(fields[5]) if len(fields) > 5 and fields[5] else 0.0,
            }
        except (ValueError, IndexError):
            continue

    if result:
        emit(f"index intraday: {len(result)} indices from Sina real-time")
    return result
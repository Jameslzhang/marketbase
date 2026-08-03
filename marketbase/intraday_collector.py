"""全市场 1 分钟 OHLCV 采集 —— 从腾讯分钟 API 拉取完整分钟线.

替代原有的离散快照追加模式，改为逐只拉取完整分钟 OHLCV 数据，
写入 intraday_minutes.parquet，支持盘中连续分钟序列的审计与事实提取.

采集策略:
  - 13:00 开始拉取，覆盖午盘到当前时刻
  - 分批并发（默认每批 30 只，间隔 0.3s）
  - 优先采集自选池 + 活跃股票
  - 审计中记录预期/实际分钟数、缺失时段、每只股票覆盖率
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from marketbase.live_workflow import fetch_tencent_minute_rows
from marketbase.minute_collector import parse_minute_rows

logger = logging.getLogger(__name__)

# 产出列定义
_MINUTE_OUTPUT_COLUMNS = [
    "code", "timestamp", "open", "high", "low", "close", "volume", "amount"
]

# 默认采集参数
_DEFAULT_BATCH_SIZE = 30
_DEFAULT_BATCH_INTERVAL = 0.3  # 秒
_DEFAULT_WORKERS = 4
_DEFAULT_START_TIME = "13:00"


def collect_intraday_minutes(
    codes: list[str],
    output_path: str | Path,
    *,
    target_date: str | None = None,
    start_time: str = _DEFAULT_START_TIME,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    batch_interval: float = _DEFAULT_BATCH_INTERVAL,
    max_workers: int = _DEFAULT_WORKERS,
    progress: Callable[[str], None] | None = None,
    priority_codes: set[str] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """采集全市场 1 分钟 OHLCV 数据，写入 intraday_minutes.parquet.

    Args:
        codes: 全市场股票代码列表
        output_path: 输出 parquet 路径
        target_date: 目标日期 (YYYY-MM-DD)，默认今天
        start_time: 采集起始时间 (HH:MM)，默认 13:00
        batch_size: 每批并发数
        batch_interval: 批次间隔（秒）
        max_workers: 线程池大小
        progress: 进度回调
        priority_codes: 优先采集的代码集合（自选池等）

    Returns:
        audit dict: 包含覆盖率、缺失时段、错误统计等
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    today = target_date or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    emit = progress or (lambda msg: None)

    # 排序：优先代码在前
    if priority_codes:
        ordered = sorted(codes, key=lambda c: (0 if c in priority_codes else 1, c))
    else:
        ordered = sorted(codes)

    total = len(ordered)
    emit(f"intraday collect: {total} stocks, start={start_time}, batch={batch_size}x{max_workers}w")

    # 收集所有分钟行
    all_rows: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
    success_count = 0
    failure_count = 0
    empty_count = 0
    errors: list[dict[str, str]] = []
    start_ts = time.monotonic()

    for batch_start in range(0, total, batch_size * max_workers):
        batch_codes = ordered[batch_start: batch_start + batch_size * max_workers]

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_single_stock_minutes, code, today, start_time): code
                for code in batch_codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        empty_count += 1
                    elif isinstance(result, list):
                        for r in result:
                            r["code"] = code
                        all_rows.extend(result)
                        success_count += 1
                    else:
                        failure_count += 1
                        errors.append({"code": code, "error": str(result)})
                except Exception as exc:
                    failure_count += 1
                    errors.append({"code": code, "error": str(exc)})

        elapsed = time.monotonic() - start_ts
        emit(f"intraday progress: {min(batch_start + batch_size * max_workers, total)}/{total} "
             f"✓{success_count} ✗{failure_count} ∅{empty_count} "
             f"elapsed={elapsed:.0f}s")

        if batch_start + batch_size * max_workers < total:
            time.sleep(batch_interval)

    # 构建 DataFrame 并写入
    if not all_rows:
        emit("intraday collect: no data collected")
        return {
            "status": "empty",
            "total_stocks": total,
            "success": 0,
            "failure": failure_count,
            "empty": empty_count,
            "rows": 0,
            "errors": errors[:50],
        }

    df = pd.DataFrame(all_rows, columns=_MINUTE_OUTPUT_COLUMNS + ["code"])
    # 确保列顺序
    df = df[_MINUTE_OUTPUT_COLUMNS].copy()
    df = df.sort_values(["code", "timestamp"]).drop_duplicates(
        subset=["code", "timestamp"], keep="last"
    ).reset_index(drop=True)

    df.to_parquet(path, index=False)

    # 审计
    audit = _audit_intraday_minutes(df, today, start_time, total, success_count, failure_count, empty_count, errors, observed_at=observed_at)
    emit(f"intraday collect: {audit['actual_minutes']}min x {audit['codes_with_data']}stocks, "
         f"coverage={audit['mean_coverage_pct']:.1f}%")

    return audit


def _fetch_single_stock_minutes(
    code: str,
    target_date: str,
    start_time: str,
) -> list[dict[str, object]] | None:
    """拉取单只股票的分钟线，解析为 OHLCV 行列表.

    返回 None 表示无数据，返回空列表表示解析失败.
    """
    try:
        raw_rows = fetch_tencent_minute_rows(code)
    except Exception:
        return []

    if not raw_rows:
        return None

    parsed = parse_minute_rows(raw_rows)
    if parsed.empty:
        return None

    # 过滤：只保留 target_date 当天且 >= start_time 的数据
    # 腾讯分钟数据格式为 HH:MM
    parsed = parsed[parsed["time"] >= start_time].copy()
    if parsed.empty:
        return None

    # 转换为 OHLCV 格式
    # 腾讯分钟数据是累计值，需要转换为每根 K 线的 OHLCV
    result = _to_ohlcv(parsed, target_date)
    return result


def _to_ohlcv(
    minute_df: pd.DataFrame,
    target_date: str,
) -> list[dict[str, object]]:
    """将腾讯累计分钟行转换为 OHLCV 格式.

    输入: time, price, volume, amount (累计值)
    输出: timestamp, open, high, low, close, volume, amount (每根K线)
    """
    if minute_df.empty or len(minute_df) < 2:
        return []

    df = minute_df.copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.sort_values("time").reset_index(drop=True)

    rows: list[dict[str, object]] = []
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        # 成交量/额差值为该分钟的值
        vol = curr["volume"] - prev["volume"]
        amt = curr["amount"] - prev["amount"]

        if vol < 0 or amt < 0:
            continue  # 跳过异常数据

        timestamp = f"{target_date}T{curr['time']}:00"

        # 该分钟的 OHLC：使用前后价格推断
        # 腾讯分钟数据只提供每分钟的最后一笔价格，所以 open=prev_price, close=curr_price
        # high/low 用 min/max 近似
        open_price = prev["price"]
        close_price = curr["price"]
        high_price = max(open_price, close_price)
        low_price = min(open_price, close_price)

        rows.append({
            "timestamp": timestamp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": vol,
            "amount": amt,
        })

    return rows


def _audit_intraday_minutes(
    df: pd.DataFrame,
    target_date: str,
    start_time: str,
    total_stocks: int,
    success_count: int,
    failure_count: int,
    empty_count: int,
    errors: list[dict[str, str]],
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """审计分钟数据质量.

    计算预期分钟数（基于 start_time 到当前已完成交易分钟，排除午休）、
    实际分钟数、缺失时段、每只股票覆盖率.

    盘中运行时，审计终点为当前已完成交易分钟（不晚于 15:00）；
    盘后运行时，审计终点为 15:00.
    """
    # 确定审计终点：当前时间与 15:00 的较小值
    start_h, start_m = map(int, start_time.split(":"))
    if observed_at is not None:
        obs_cn = observed_at.astimezone(timezone(timedelta(hours=8)))
        obs_h, obs_m = obs_cn.hour, obs_cn.minute
        if obs_h >= 15:
            end_h, end_m = 15, 0
        elif obs_h < 9 or (obs_h == 9 and obs_m < 30):
            end_h, end_m = 15, 0
        else:
            end_h, end_m = obs_h, obs_m
    else:
        end_h, end_m = 15, 0
    end_time = f"{end_h:02d}:{end_m:02d}

    # 从 start_time 到 end_time 的总分钟数（排除午休）
    total_minutes = (end_h * 60 + end_m) - (start_h * 60 + start_m)
    if start_h < 12:
        lunch_start = 11 * 60 + 30
        lunch_end = 13 * 60
        if total_minutes > 0:
            overlap = min(end_h * 60 + end_m, lunch_end) - max(start_h * 60 + start_m, lunch_start)
            if overlap > 0:
                total_minutes -= overlap

    audit: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "start_time": start_time,
        "end_time": end_time,
        "expected_minutes": total_minutes,
        "total_stocks": total_stocks,
        "success": success_count,
        "failure": failure_count,
        "empty": empty_count,
        "errors": errors[:50],
    }

    if df.empty:
        audit["actual_minutes"] = 0
        audit["codes_with_data"] = 0
        audit["mean_coverage_pct"] = 0.0
        audit["missing_periods"] = [f"{start_time}-{end_time}"]
        return audit

    # 实际唯一分钟数
    unique_times = sorted(df["timestamp"].unique())
    audit["actual_minutes"] = len(unique_times)
    audit["earliest_time"] = unique_times[0] if unique_times else None
    audit["latest_time"] = unique_times[-1] if unique_times else None

    # 缺失时段检测
    present_times = set(pd.to_datetime(unique_times))
    expected_times = _generate_trading_minutes(target_date, start_time, end_time)
    missing = sorted(set(expected_times) - present_times)
    audit["missing_minute_count"] = len(missing)
    audit["missing_periods"] = _group_missing_periods(missing, target_date)[:20]

    # 时间连续性断点
    if len(unique_times) > 1:
        times = pd.to_datetime(unique_times)
        gaps = times[1:] - times[:-1]
        breaks = gaps[gaps > pd.Timedelta(minutes=2)]
        audit["continuity_break_count"] = len(breaks)
        audit["continuity_breaks"] = [
            {"after": str(times[i]), "gap_minutes": round(g.total_seconds() / 60, 1)}
            for i, g in breaks.items()
        ][:20]
    else:
        audit["continuity_break_count"] = 0
        audit["continuity_breaks"] = []

    # 每只股票覆盖率
    code_minutes = df.groupby("code")["timestamp"].nunique()
    audit["codes_with_data"] = len(code_minutes)
    coverage = code_minutes / max(total_minutes, 1)
    audit["mean_coverage_pct"] = round(float(coverage.mean()) * 100, 1)
    audit["min_coverage_pct"] = round(float(coverage.min()) * 100, 1)
    audit["max_coverage_pct"] = round(float(coverage.max()) * 100, 1)
    audit["codes_full"] = int((coverage >= 0.95).sum())
    audit["codes_partial"] = int(((coverage < 0.95) & (coverage > 0)).sum())
    audit["codes_none"] = int((coverage == 0).sum())

    return audit


def _generate_trading_minutes(
    target_date: str,
    start_time: str,
    end_time: str,
) -> list[pd.Timestamp]:
    """生成交易时段内的所有分钟（排除午休 11:30-13:00）."""
    start_h, start_m = map(int, start_time.split(":"))
    end_h, end_m = map(int, end_time.split(":"))

    tz = timezone(timedelta(hours=8))
    start_ts = pd.Timestamp(f"{target_date}T{start_time}:00", tz=tz)
    end_ts = pd.Timestamp(f"{target_date}T{end_time}:00", tz=tz)

    lunch_start = pd.Timestamp(f"{target_date}T11:30:00", tz=tz)
    lunch_end = pd.Timestamp(f"{target_date}T13:00:00", tz=tz)

    minutes = []
    current = start_ts
    while current <= end_ts:
        if not (lunch_start <= current < lunch_end):
            minutes.append(current)
        current += pd.Timedelta(minutes=1)
    return minutes


def _group_missing_periods(
    missing: list[pd.Timestamp],
    target_date: str,
) -> list[str]:
    """将缺失分钟列表合并为连续的时段."""
    if not missing:
        return []

    periods: list[str] = []
    start = missing[0]
    end = missing[0]

    for t in missing[1:]:
        if t == end + pd.Timedelta(minutes=1):
            end = t
        else:
            periods.append(f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
            start = t
            end = t
    periods.append(f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
    return periods


def load_intraday_minutes(
    input_path: str | Path,
    *,
    target_date: str | None = None,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """从 intraday_minutes.parquet 加载分钟数据.

    Args:
        input_path: parquet 文件路径
        target_date: 过滤目标日期
        codes: 过滤股票代码

    Returns:
        DataFrame with columns: code, timestamp, open, high, low, close, volume, amount
    """
    path = Path(input_path)
    if not path.exists():
        return pd.DataFrame(columns=_MINUTE_OUTPUT_COLUMNS)

    df = pd.read_parquet(path)

    if target_date and "timestamp" in df.columns:
        df["_date"] = pd.to_datetime(df["timestamp"]).dt.date.astype(str)
        df = df[df["_date"] == target_date].drop(columns=["_date"])

    if codes and "code" in df.columns:
        df = df[df["code"].isin(codes)]

    return df.sort_values(["code", "timestamp"]).reset_index(drop=True)
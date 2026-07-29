"""客观数据本地采集入口与请求补数命令行。

采集全市场快照、250 日日线、中性指标、分类映射和审计报告。
输出纯数据交接文件，不包含任何策略分、排名、推荐或交易结论。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import Any

# ── Windows / POSIX 文件锁 ───────────────────────────────────────────
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX 分支使用 fcntl
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 分支使用 msvcrt
    fcntl = None

import pandas as pd

from marketbase.classification_collector import collect_classification
from marketbase.classify import build_classification_map
from marketbase.daily import fetch_daily_history
from marketbase.daily_collector import (
    DailyCollectionReport,
    DailyProgressEvent,
    _MIN_INDICATOR_ROWS,
    collect_daily_universe,
)
from marketbase.data_request import load_data_request, write_data_response
from marketbase.market_collector import MarketCollectionResult, collect_market_snapshot
from marketbase.minute_collector import collect_requested_data
from marketbase.security_master import collect_security_master
from marketbase.tradability import enrich_tradability
from marketbase.volume_ratio import compute_volume_ratios_batch
from marketbase.market_breadth import compute_market_breadth
from marketbase.intraday import append_minute_snapshot, build_intraday_sequence, audit_minute_sequence


# ── 指标字段定义 ─────────────────────────────────────────────────────
_INDICATOR_VALUE_FIELDS = (
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
)
_INDICATOR_FIELDS = (
    *_INDICATOR_VALUE_FIELDS,
    "input_rows",
    "first_date",
    "last_date",
    "calculated_at",
)
_BAR_WIDTH = 30
_PROGRESS_STATE: dict[str, object] = {"last_bar_len": 0}
_LATEST_THREAD_LOCK = threading.Lock()


def _ts() -> str:
    """短时间戳 HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def _render_bar(completed: int, total: int, width: int = _BAR_WIDTH) -> str:
    """Render a █░ progress bar string."""
    if total <= 0:
        return ""
    ratio = min(completed / total, 1.0)
    filled = int(ratio * width)
    pct = ratio * 100
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {pct:5.1f}% ({completed}/{total})"


def _clear_progress_line() -> None:
    """Erase the last progress line from the terminal."""
    last_len = int(_PROGRESS_STATE.get("last_bar_len", 0))
    if last_len > 0 and sys.stdout.isatty():
        sys.stdout.write("\r" + " " * last_len + "\r")
        sys.stdout.flush()
    _PROGRESS_STATE["last_bar_len"] = 0


def _write_progress_line(line: str) -> None:
    """Write or overwrite a progress line (no newline, uses \\r)."""
    _clear_progress_line()
    if sys.stdout.isatty():
        sys.stdout.write(line)
        sys.stdout.flush()
        _PROGRESS_STATE["last_bar_len"] = len(line)
    else:
        # pipe/file — print normally
        print(line, flush=True)


_FORBIDDEN_TERMS = (
    "candidate",
    "recommend",
    "buy",
    "sell",
    "probability",
    "rank",
    "score",
    "mainline",
    "tier",
    "候选",
    "推荐",
    "买入",
    "卖出",
    "概率",
    "排名",
    "评分",
    "主线",
    "梯队",
)


def run_collection(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    progress: Callable[[str], None] = print,
    providers: Mapping[str, object] | None = None,
    phase: str = "post_close",
    force_refresh: bool = False,
) -> dict[str, object]:
    """Collect current market facts and associated daily/cache evidence."""
    root = Path(data_root).expanduser().resolve()
    observed_at = _observed_at(now)
    configured = dict(providers or {})

    # --- single-instance process lock ---
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".workflow.lock"
    lock_handle = lock_path.open("a+b")
    lock_handle.seek(0)
    acquired = _try_lock_nonblocking(lock_handle)
    if not acquired:
        lock_handle.close()
        print("采集已在运行中（检测到 .workflow.lock），第二个实例退出。", flush=True)
        sys.exit(0)
    try:
        return _run_collection_locked(root, observed_at, configured, progress, phase=phase, force_refresh=force_refresh)
    finally:
        _unlock_file(lock_handle)
        lock_handle.close()


def _try_lock_nonblocking(handle: Any) -> bool:
    """尝试获取文件锁（非阻塞），成功返回 True，防止多实例并发运行."""
    if msvcrt is not None:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            return False
    return True  # no locking mechanism available — proceed anyway


def _run_collection_locked(
    root: Path,
    observed_at: datetime,
    configured: dict[str, object],
    progress: Callable[[str], None],
    phase: str = "post_close",
    force_refresh: bool = False,
) -> dict[str, object]:
    """持有文件锁后执行核心采集流程：快照 → 日线 → 指标 → 量比 → 审计 → 分类 → 交接."""
    collection_started_at = datetime.now().astimezone().isoformat()
    run_dir = _create_run_directory(root, observed_at, phase=phase)
    log_path = run_dir / "workflow.log"

    def emit(message: str) -> None:
        _clear_progress_line()
        text = f"{_ts()}  {_neutral_text(message)}"
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")

    emit("开始采集")
    cache_root = root / "cache"

    # ① 实时行情快照
    frame, codes, result = _run_market_collection(root, observed_at, emit, configured)
    bse_codes = set(frame.loc[frame["market"] == "bj", "code"].tolist()) if "market" in frame.columns else set()
    # ①.5 分钟快照追加与 VWAP 计算
    minute_audit = _run_minute_snapshot(frame, cache_root, observed_at, emit)
    # ② 日线历史与指标计算
    indicators_df, daily_report = _run_daily_collection(codes, cache_root, observed_at, emit, configured, bse_codes=bse_codes, force_refresh=force_refresh)
    # ③ 量比实时计算
    frame = _run_volume_ratio(frame, cache_root, observed_at, emit)
    # ③.5 交易可执行性标注
    frame = _run_tradability(frame, root, emit)
    # ④ 审计与分类
    market_audit, classification, classification_audit = _run_audit_and_classification(
        frame, observed_at, result, root, configured, phase=phase
    )
    # ④.5 行业/概念字段补充
    frame = _run_enrich_classification(frame, classification, emit)
    emit("采集完成")

    # --- 市场广度汇总 ---
    breadth = compute_market_breadth(frame)
    _write_json_atomic(run_dir / "market_breadth.json", breadth)
    emit(f"市场广度: 涨{breadth.get('full_market', {}).get('advance_count', 0)} "
         f"跌{breadth.get('full_market', {}).get('decline_count', 0)} "
         f"平{breadth.get('full_market', {}).get('unchanged_count', 0)}")

    summary = _write_outputs_and_manifest(
        run_dir, root, observed_at, frame, indicators_df, classification,
        market_audit, classification_audit, result, daily_report, codes,
        minute_audit, collection_started_at,
    )
    return summary


def fulfill_request(
    *,
    request_path: str | Path,
    response_path: str | Path,
    data_root: str | Path,
    now: datetime | None = None,
    providers: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Fulfill one validated request using only the daily cache and request scopes."""
    observed_at = _observed_at(now)
    request = load_data_request(request_path, today=observed_at.date())
    configured = dict(providers or {})
    collection_args: dict[str, object] = {
        "daily_cache_root": Path(data_root).expanduser().resolve() / "cache" / "daily",
        "daily_fetcher": configured.get("daily_fetcher", fetch_daily_history),
        "now": observed_at,
    }
    if "minute_fetcher" in configured:
        collection_args["minute_fetcher"] = configured["minute_fetcher"]
    payload = collect_requested_data(request, **collection_args)
    write_data_response(response_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：默认全量采集，也支持 collect-classify / refresh-master / fulfill-request 子命令."""
    parser = argparse.ArgumentParser(description="MarketBase 客观数据入口")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--phase", type=str, default="post_close",
                        help="采集阶段: post_close | 13:00 | 14:30 | 15:30")
    parser.add_argument("--force-refresh", action="store_true", default=False,
                        help="强制重新拉取日线数据，忽略缓存")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("collect")
    request_parser = subcommands.add_parser("fulfill-request")
    request_parser.add_argument("--request", type=Path)
    request_parser.add_argument("--response", type=Path)
    classify_parser = subcommands.add_parser("collect-classify")
    classify_parser.add_argument("--output", type=Path)
    master_parser = subcommands.add_parser("refresh-master")
    master_parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    default_root = Path(__file__).resolve().parent / "data" / "daily_runs"

    try:
        if arguments.command == "collect-classify":
            root = arguments.data_root or default_root
            output = arguments.output or root / "classification_source.csv"
            df = collect_classification(output)
            print(f"分类数据采集完成: {len(df)} 行, {df['industry'].nunique()} 行业")
            return 0
        if arguments.command == "refresh-master":
            root = arguments.data_root or default_root
            output = arguments.output or root / "cache" / "security_master.csv"
            df = collect_security_master(output)
            print(f"证券主表刷新完成: {len(df)} 只股票, {df['market'].nunique()} 市场")
            return 0
        if arguments.command == "fulfill-request":
            root = arguments.data_root or default_root
            fulfill_request(
                request_path=arguments.request or root / "codex_data_request.json",
                response_path=arguments.response or root / "codex_data_response.json",
                data_root=root,
            )
            print("客观数据请求补数完成")
            return 0
        summary = run_collection(
            data_root=getattr(arguments, "data_root", None) or default_root,
            phase=getattr(arguments, "phase", "post_close"),
            force_refresh=getattr(arguments, "force_refresh", False),
        )
        print(
            "客观数据采集完成: "
            f"市场行数={summary['market_rows']} "
            f"日线成功={summary['daily_success']} 日线失败={summary['daily_failure']}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - command boundary prints one neutral failure.
        print(f"数据采集错误: {_neutral_text(str(exc) or type(exc).__name__)}")
        return 1

def _run_minute_snapshot(frame, cache_root, observed_at, emit):
    minute_audit = {"status": "collected"}
    intraday_path = cache_root / "intraday_1m.parquet"
    try:
        append_minute_snapshot(frame, intraday_path)
        emit("minutes appended to intraday_1m.parquet")
    except Exception as exc:
        minute_audit["append_error"] = _neutral_text(str(exc) or type(exc).__name__)
        emit(f"minute append failed: {minute_audit['append_error']}")
        return minute_audit
    try:
        seq = build_intraday_sequence(intraday_path)
        if not seq.empty:
            min_audit = audit_minute_sequence(seq, observed_at=observed_at)
            minute_audit["sequence_audit"] = min_audit
            minute_audit["total_minutes"] = min_audit.get("actual_minutes", 0)
            minute_audit["total_stocks"] = int(seq["code"].nunique())
            emit(f"minute seq: {minute_audit['total_minutes']}min x {minute_audit['total_stocks']}stocks")
    except Exception as exc:
        minute_audit["sequence_error"] = _neutral_text(str(exc) or type(exc).__name__)
    try:
        vwap = frame["amount"] / frame["volume"].replace(0, float("nan"))
        minute_audit["vwap_coverage"] = int(vwap.notna().sum())
        minute_audit["vwap_total"] = len(frame)
        emit(f"VWAP computable: {minute_audit['vwap_coverage']}/{len(frame)}")
    except Exception as exc:
        minute_audit["vwap_error"] = _neutral_text(str(exc) or type(exc).__name__)
    return minute_audit


def _run_enrich_classification(frame, classification, emit):
    if classification.empty:
        return frame
    try:
        class_indexed = classification.set_index("code")
        enriched = 0
        for col in ("industry", "concepts"):
            if col not in frame.columns:
                continue
            missing = frame[col].isna() | frame[col].astype(str).str.strip().isin({"", "nan", "None", "<NA>"})
            if not missing.any():
                continue
            fill_values = frame.loc[missing, "code"].map(
                class_indexed[col].replace("", float("nan")).dropna()
            )
            filled = fill_values.notna().sum()
            frame.loc[missing, col] = frame.loc[missing, col].fillna(fill_values)
            enriched += filled
        if enriched:
            emit(f"class enrichment: {enriched} rows filled")
    except Exception as exc:
        emit(f"class enrichment failed: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame



def _run_market_collection(
    root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
    configured: dict[str, object],
) -> tuple[pd.DataFrame, list[str], MarketCollectionResult]:
    """Acquire market snapshot and return frame, codes, and raw result."""
    cache_root = root / "cache"
    market_cache_path = cache_root / "market_snapshot.json"
    market_collector = configured.get("market_collector", collect_market_snapshot)
    result = _call_market_collector(
        market_collector,
        cache_path=market_cache_path,
        now=observed_at,
        progress=emit,
    )
    frame = result.frame.copy()
    _ensure_market_cache(market_cache_path, observed_at, result, frame)
    codes = _unique_codes(frame)
    emit(f"全市场股票数 {len(frame)}")
    return frame, codes, result


def _run_daily_collection(
    codes: list[str],
    cache_root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
    configured: dict[str, object],
    bse_codes: set[str] | None = None,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, DailyCollectionReport]:
    """Collect daily history for all codes and return indicators DataFrame."""
    daily_fetcher = configured.get("daily_fetcher", fetch_daily_history)
    daily_report = collect_daily_universe(
        codes,
        cache_root=cache_root / "daily",
        checkpoint_path=cache_root / "daily_checkpoint.json",
        lookback=260,
        fetcher=daily_fetcher,
        progress=lambda event: _write_progress_line(_daily_progress_message(event)),
        now=observed_at,
        force_refresh=force_refresh,
    )
    _clear_progress_line()
    cache_valid = daily_report.success_count + daily_report.cache_hit_count
    short_history_count = len(daily_report.short_history)
    indicator_count = len(daily_report.indicators)
    indicator_valid_count = sum(
        1 for entry in daily_report.indicators
        if isinstance(entry, dict) and any(
            entry.get(field) is not None and not (isinstance(entry.get(field), float) and math.isnan(float(entry.get(field))))
            for field in _INDICATOR_VALUE_FIELDS
        )
    )
    indicator_insufficient_count = len(daily_report.indicator_insufficient)
    bse_set = bse_codes or set()
    bse_short = [
        entry for entry in daily_report.short_history
        if isinstance(entry, dict) and str(entry.get("code", "")) in bse_set
    ]
    bse_insufficient = [
        entry for entry in daily_report.indicator_insufficient
        if isinstance(entry, dict) and str(entry.get("code", "")) in bse_set
    ]
    emit(f"日线缓存有效 {cache_valid}/{daily_report.total_count}")
    if short_history_count:
        emit(f"历史长度不足 {short_history_count}")
    if bse_short:
        emit(f"北交所历史不足 {len(bse_short)}/{len(bse_short) + len([e for e in daily_report.short_history if e not in bse_short])}（北交所数据源仅返回1根日线）")
    emit(f"指标可计算 {indicator_valid_count}/{daily_report.total_count}（记录总数{indicator_count}）")
    if indicator_insufficient_count:
        emit(f"指标数据不足 {indicator_insufficient_count}{'（北交所' + str(len(bse_insufficient)) + '只）' if bse_insufficient else ''}")
    if indicator_valid_count < indicator_count:
        emit(f"指标全部为空 {indicator_count - indicator_valid_count}（含北交所{bse_indicator_empty}只）" if (bse_indicator_empty := sum(
            1 for entry in daily_report.indicators
            if isinstance(entry, dict) and str(entry.get("code", "")) in bse_set
            and not any(
                entry.get(field) is not None and not (isinstance(entry.get(field), float) and math.isnan(float(entry.get(field))))
                for field in _INDICATOR_VALUE_FIELDS
            )
        )) else f"指标全部为空 {indicator_count - indicator_valid_count}")
    if daily_report.failure_count:
        emit(f"日线采集失败 {daily_report.failure_count}")
    emit(f"日线耗时{daily_report.elapsed_seconds:.0f}秒")
    rate = (daily_report.total_count / daily_report.elapsed_seconds * 60) if daily_report.elapsed_seconds > 0 else 0
    emit(
        f"daily_completed={cache_valid}/{daily_report.total_count}"
        f" wall_time={observed_at.isoformat()}"
        f" elapsed={daily_report.elapsed_seconds:.1f}"
        f" rate={rate:.0f}/min"
        f" eta=0"
        f" cache_hits={daily_report.cache_hit_count}"
        f" failures={daily_report.failure_count}"
        f" pending=0"
        f" current_code="
        f" current_source="
        f" last_error="
    )
    indicators_df = pd.DataFrame(
        daily_report.indicators,
        columns=("code", *_INDICATOR_FIELDS),
    )
    return indicators_df, daily_report


def _run_volume_ratio(
    frame: pd.DataFrame,
    cache_root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
) -> pd.DataFrame:
    """Compute volume ratios locally and return updated frame."""
    try:
        frame = compute_volume_ratios_batch(frame, cache_root / "daily", observed_at)
        vr_count = int(frame["volume_ratio"].notna().sum())
        emit(f"量比本地计算完成 覆盖{vr_count}/{len(frame)}")
    except Exception as exc:
        emit(f"量比本地计算失败: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame


def _run_tradability(
    frame: pd.DataFrame,
    root: Path,
    emit: Callable[[str], None],
) -> pd.DataFrame:
    """Enrich snapshot with tradability fields (ST, board, limit status, listing days)."""
    try:
        sm_path = root / "cache" / "security_master.csv"
        sm_df = pd.read_csv(sm_path, dtype=str, keep_default_na=False) if sm_path.is_file() else None
        frame = enrich_tradability(frame, security_master_df=sm_df)
        st_count = int(frame["is_st"].sum()) if "is_st" in frame.columns else 0
        suspended_count = int(frame["is_suspended"].sum()) if "is_suspended" in frame.columns else 0
        limit_up_count = int(frame["is_limit_up"].sum()) if "is_limit_up" in frame.columns else 0
        limit_down_count = int(frame["is_limit_down"].sum()) if "is_limit_down" in frame.columns else 0
        emit(f"交易可执行性标注: ST={st_count} 停牌={suspended_count} 涨停={limit_up_count} 跌停={limit_down_count}")
    except Exception as exc:
        emit(f"交易可执行性标注失败: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame


def _run_audit_and_classification(
    frame: pd.DataFrame,
    observed_at: datetime,
    result: MarketCollectionResult,
    root: Path,
    configured: dict[str, object],
    phase: str = "post_close",
) -> tuple[dict[str, object], pd.DataFrame, dict[str, object]]:
    """Run audit and classification, returning (market_audit, classification, classification_audit)."""
    from marketbase.data_audit import audit_market_snapshot

    market_audit = audit_market_snapshot(
        frame,
        observed_at=observed_at,
        provider_errors=result.audit.get("provider_errors", []),
        audit_phase=phase,
    )
    market_audit["bse_audit"] = result.audit.get("bse_audit", {})

    existing_map = _existing_map(root, configured.get("existing_map"))
    supply_chain_path = _supply_chain_path(root, configured.get("supply_chain_path"))
    classification, classification_audit = build_classification_map(
        frame,
        existing_map=existing_map,
        supply_chain_path=supply_chain_path,
    )
    return market_audit, classification, classification_audit


def _quality_status(
    market_audit: dict[str, object],
    daily_report: DailyCollectionReport,
    bse_codes_set: set[str],
) -> str:
    """Return 'pass', 'partial', or 'blocked' based on coverage thresholds."""
    # Compute market coverage from field_coverage if available, otherwise from total_rows vs field nulls
    field_coverage = market_audit.get("field_coverage", {})
    if isinstance(field_coverage, dict) and field_coverage:
        market_coverage = sum(float(v) for v in field_coverage.values()) / max(len(field_coverage), 1)
    else:
        market_coverage = 1.0
    daily_success = daily_report.success_count + daily_report.cache_hit_count
    daily_rate = daily_success / max(daily_report.total_count, 1)
    bse_total = len(bse_codes_set)
    bse_unavailable = sum(
        1 for entry in daily_report.short_history
        if isinstance(entry, dict) and entry.get("code", "") in bse_codes_set
    )
    bse_rate = (bse_total - bse_unavailable) / max(bse_total, 1) if bse_total else 1.0

    if market_coverage < 0.5 or daily_rate < 0.5:
        return "blocked"
    if market_coverage < 0.95 or daily_rate < 0.95 or (bse_total > 0 and bse_rate < 0.9):
        return "partial"
    return "pass"


def _write_outputs_and_manifest(
    run_dir: Path,
    root: Path,
    observed_at: datetime,
    frame: pd.DataFrame,
    indicators_df: pd.DataFrame,
    classification: pd.DataFrame,
    market_audit: dict[str, object],
    classification_audit: dict[str, object],
    result: MarketCollectionResult,
    daily_report: DailyCollectionReport,
    codes: list[str],
    minute_audit: dict[str, object] | None = None,
    collection_started_at: str | None = None,
) -> dict[str, object]:
    """Write all output files, manifest, and latest handoff; return summary."""
    collection_completed_at = datetime.now().astimezone().isoformat()
    _write_csv_atomic(run_dir / "market_snapshot.csv", frame)
    _write_json_atomic(
        run_dir / "market_snapshot.json",
        {"schema_version": 1, "generated_at": observed_at.isoformat(), "rows": _frame_records(frame)},
    )
    # Sort indicators by code before writing
    indicators_df_sorted = indicators_df.sort_values("code", ignore_index=True) if not indicators_df.empty else indicators_df
    _write_csv_atomic(run_dir / "daily_indicators.csv", indicators_df_sorted)
    _write_csv_atomic(run_dir / "classification_map.csv", classification)
    _write_csv_atomic(root / "classification_map.csv", classification)

    total_codes = len(codes)
    cache_coverage = total_codes - len(daily_report.invalid_or_missing_cache)
    daily_audit_payload = asdict(daily_report)
    daily_audit_payload["checkpoint_path"] = str(daily_report.checkpoint_path.resolve())
    daily_audit_payload["cache_root"] = str(daily_report.cache_root.resolve())
    daily_audit_payload["errors"] = {code: _neutral_text(error) for code, error in daily_report.errors.items()}
    daily_audit_payload["cache_coverage_count"] = cache_coverage
    daily_audit_payload["cache_coverage_rate"] = cache_coverage / total_codes if total_codes else 0.0
    # Remove full indicator records to avoid file bloat — keep only counts
    daily_audit_payload.pop("indicators", None)
    daily_audit = _json_value(daily_audit_payload)

    # --- indicator quality breakdown ---
    indicator_records = len(daily_report.indicators)
    indicator_any_valid = sum(
        1 for entry in daily_report.indicators
        if isinstance(entry, dict) and any(
            entry.get(field) is not None and not (isinstance(entry.get(field), float) and math.isnan(float(entry.get(field))))
            for field in _INDICATOR_VALUE_FIELDS
        )
    )
    indicator_all_valid = sum(
        1 for entry in daily_report.indicators
        if isinstance(entry, dict) and all(
            entry.get(field) is not None and not (isinstance(entry.get(field), float) and math.isnan(float(entry.get(field))))
            for field in _INDICATOR_VALUE_FIELDS
        )
    )
    indicator_all_empty = indicator_records - indicator_any_valid
    # BSE codes from market snapshot frame (not prefix-based guessing)
    bse_codes_set = set(frame.loc[frame["market"] == "bj", "code"].tolist()) if "market" in frame.columns else set()
    bse_history_unavailable = sorted(
        c for c in bse_codes_set
        if c in set(daily_report.errors.keys())
        or any(
            entry.get("code") == c and entry.get("actual_rows", 0) < _MIN_INDICATOR_ROWS
            for entry in daily_report.short_history
        )
    )

    audit = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
        "quality_status": _quality_status(market_audit, daily_report, bse_codes_set),
        "market": _json_value(market_audit),
        "daily": daily_audit,
        "indicators": {
            "records_generated": indicator_records,
            "any_valid": indicator_any_valid,
            "all_valid": indicator_all_valid,
            "all_empty": indicator_all_empty,
            "insufficient_history_count": len(daily_report.indicator_insufficient),
            "errors": [],
        },
        "bse": {
            "total": len(bse_codes_set),
            "history_unavailable": bse_history_unavailable,
            "history_unavailable_count": len(bse_history_unavailable),
        },
        "classification": _json_value(classification_audit),
        "provider_errors": _provider_errors(result, daily_report),
        "minute_request": minute_audit or {"request_status": "not_requested"},
        "stale_daily": _stale_daily_summary(daily_report, observed_at),
    }
    _write_json_atomic(run_dir / "data_audit.json", audit)

    files = _file_records(
        run_dir,
        {
            "market_snapshot_csv": len(frame),
            "market_snapshot_json": len(frame),
            "daily_indicators": len(indicators_df),
            "classification_map": len(classification),
            "market_breadth": 1,
            "data_audit": 1,
            "workflow_log": 0,
        },
    )
    manifest = {
        "schema_version": 1,
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
        "run_dir": str(run_dir),
        "files": files,
        "cache_paths": _cache_paths(root),
        "errors": _error_summary(audit),
    }
    _write_json_atomic(run_dir / "manifest.json", manifest)
    latest_path = root / "latest_codex_input.json"
    _publish_latest(
        latest_path,
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "collection_started_at": collection_started_at,
            "collection_completed_at": collection_completed_at,
            "run_dir": str(run_dir),
            "market_snapshot_path": str((run_dir / "market_snapshot.json").resolve()),
            "daily_indicators_path": str((run_dir / "daily_indicators.csv").resolve()),
            "classification_map_path": str((run_dir / "classification_map.csv").resolve()),
            "market_breadth_path": str((run_dir / "market_breadth.json").resolve()),
            "data_audit_path": str((run_dir / "data_audit.json").resolve()),
            "manifest_path": str((run_dir / "manifest.json").resolve()),
            "cache_paths": _cache_paths(root),
            "quality_status": audit["quality_status"],
            "market_rows": len(frame),
            "indicator_rows": indicator_records,
            "indicator_any_valid": indicator_any_valid,
            "indicator_all_empty": indicator_all_empty,
            "bse_total": len(bse_codes_set),
            "bse_history_unavailable": len(bse_history_unavailable),
            "daily_success": daily_report.success_count + daily_report.cache_hit_count,
            "daily_failure": daily_report.failure_count,
            "cache_hits": daily_report.cache_hit_count,
        },
    )

    return {
        "run_dir": str(run_dir),
        "generated_at": observed_at.isoformat(),
        "collection_started_at": collection_started_at,
        "collection_completed_at": collection_completed_at,
        "market_rows": len(frame),
        "daily_success": daily_report.success_count + daily_report.cache_hit_count,
        "daily_failure": daily_report.failure_count,
        "indicator_rows": len(indicators_df),
        "classification_rows": len(classification),
        "latest_input_path": str(latest_path),
        "files": files,
    }


def _call_market_collector(
    collector: object,
    *,
    cache_path: Path,
    now: datetime,
    progress: Callable[[str], None],
) -> MarketCollectionResult:
    if not callable(collector):
        raise TypeError("market_collector must be callable")
    result = collector(cache_path=cache_path, now=now, progress=progress)
    if not isinstance(getattr(result, "frame", None), pd.DataFrame):
        raise TypeError("market_collector returned no market frame")
    return result


def _ensure_market_cache(
    path: Path,
    observed_at: datetime,
    result: MarketCollectionResult,
    frame: pd.DataFrame,
) -> None:
    if path.is_file():
        return
    _write_json_atomic(
        path,
        {
            "schema_version": 1,
            "generated_at": observed_at.isoformat(),
            "report": _json_value(getattr(result, "report", {})),
            "audit": _json_value(getattr(result, "audit", {})),
            "rows": _frame_records(frame),
        },
    )


def _existing_map(root: Path, configured: object) -> pd.DataFrame | None:
    if configured is not None:
        return configured if isinstance(configured, pd.DataFrame) else None
    path = root / "classification_source.csv"
    if not path.is_file():
        return None
    try:
        return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False, comment="#")
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError):
        return None


def _supply_chain_path(root: Path, configured: object) -> str | Path | None:
    if configured is not None:
        return configured if isinstance(configured, (str, Path)) else None
    path = root / "supply_chain_map.csv"
    return path if path.is_file() else None


def _daily_progress_message(event: DailyProgressEvent) -> str:
    completed = event.completed
    total = event.total
    success = event.success_count + event.cache_hit_count
    fail = event.failure_count
    rate = event.rate_per_minute
    if event.eta_seconds is not None and event.eta_seconds > 0:
        eta_min = event.eta_seconds / 60
        eta_str = f"ETA {eta_min:.1f}分"
    else:
        eta_str = ""
    bar = _render_bar(completed, total)
    parts = [bar, f"⋮ {rate:.0f}只/分", f"{success}✓ {fail}✗"]
    if eta_str:
        parts.append(eta_str)
    return "  ".join(parts)


def _stale_daily_summary(
    daily_report: DailyCollectionReport,
    observed_at: datetime,
) -> dict[str, object]:
    """Summarize stocks whose latest daily data is not from the current trade date."""
    from datetime import timezone, timedelta

    trade_date = observed_at.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    stale_codes = [
        code for code, date_str in daily_report.latest_date_distribution.items()
        if str(date_str) != trade_date
    ]
    return {
        "expected_latest_date": trade_date,
        "total_stocks": daily_report.total_count,
        "stale_count": len(stale_codes),
        "stale_codes": stale_codes[:50],
        "stale_codes_truncated": len(stale_codes) > 50,
    }


def _provider_errors(
    result: MarketCollectionResult, report: DailyCollectionReport
) -> list[str]:
    values = list(result.audit.get("provider_errors", []))
    values.extend(report.errors.values())
    return [_neutral_text(str(value)) for value in values]


def _cache_paths(root: Path) -> dict[str, str]:
    cache = root / "cache"
    return {
        "market_snapshot": str((cache / "market_snapshot.json").resolve()),
        "daily": str((cache / "daily").resolve()),
        "daily_checkpoint": str((cache / "daily_checkpoint.json").resolve()),
        "classification_map": str((root / "classification_map.csv").resolve()),
    }


def _file_records(run_dir: Path, rows: Mapping[str, int]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for key, count in rows.items():
        name = {
            "market_snapshot_csv": "market_snapshot.csv",
            "market_snapshot_json": "market_snapshot.json",
            "daily_indicators": "daily_indicators.csv",
            "classification_map": "classification_map.csv",
            "market_breadth": "market_breadth.json",
            "data_audit": "data_audit.json",
            "workflow_log": "workflow.log",
        }[key]
        path = run_dir / name
        result[key] = {
            "name": name,
            "rows": _line_count(path) if key == "workflow_log" else count,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return result


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline=None) as handle:
        return sum(1 for _ in handle)


def _error_summary(audit: Mapping[str, object]) -> dict[str, int]:
    daily = audit.get("daily", {})
    indicators = audit.get("indicators", {})
    classification = audit.get("classification", {})
    return {
        "daily": int(daily.get("failure_count", 0)) if isinstance(daily, dict) else 0,
        "indicators": int(indicators.get("failure_count", 0)) if isinstance(indicators, dict) else 0,
        "classification": len(classification.get("errors", [])) if isinstance(classification, dict) else 0,
    }


def _create_run_directory(root: Path, observed_at: datetime, phase: str = "post_close") -> Path:
    parent = root / observed_at.date().isoformat()
    parent.mkdir(parents=True, exist_ok=True)
    phase_slug = phase.replace(":", "").replace("_", "")
    stem = f"{observed_at:%H%M%S}_{phase_slug}_objective_data"
    suffix = 1
    while True:
        name = stem if suffix == 1 else f"{stem}_{suffix}"
        path_option = parent / name
        try:
            path_option.mkdir(exist_ok=False)
        except FileExistsError:
            suffix += 1
            continue
        return path_option.resolve()


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_text_atomic(
        path,
        json.dumps(_neutralize_output(_json_value(payload)), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
    )


def _publish_latest(path: Path, payload: Mapping[str, object]) -> bool:
    generated_at = _parse_generated_at(payload.get("generated_at"))
    if generated_at is None:
        raise ValueError("latest handoff generated_at is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _LATEST_THREAD_LOCK, lock_path.open("a+b") as lock_handle:
        if lock_handle.seek(0, os.SEEK_END) == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        lock_handle.seek(0)
        _lock_file(lock_handle)
        try:
            existing_at = _existing_generated_at(path)
            if existing_at is not None and generated_at < existing_at:
                return False
            _write_json_atomic(path, payload)
            return True
        finally:
            _unlock_file(lock_handle)


def _lock_file(handle: Any) -> None:
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


def _unlock_file(handle: Any) -> None:
    if msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    raise RuntimeError("no supported file-locking mechanism is available")


def _existing_generated_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_generated_at(payload.get("generated_at")) if isinstance(payload, dict) else None


def _parse_generated_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _atomic_replace(temp_path: Path, target_path: Path, *, retries: int = 5, delay: float = 0.1) -> None:
    """Atomically replace *target_path* with *temp_path*, retrying on Windows lock errors."""
    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def _write_csv_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False
        ) as handle:
            _neutralize_frame(frame).to_csv(handle, index=False)
            temporary = Path(handle.name)
        _atomic_replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        _atomic_replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return _json_value(frame.to_dict(orient="records"))


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if pd.isna(value):
        return None
    return value


def _neutralize_output(value: Any) -> Any:
    if isinstance(value, str):
        return _neutral_text(value)
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            base = _neutral_text(key)
            key_option = base
            suffix = 2
            while key_option in normalized:
                key_option = f"{base}_{suffix}"
                suffix += 1
            normalized[key_option] = _neutralize_output(item)
        return normalized
    if isinstance(value, list):
        return [_neutralize_output(item) for item in value]
    return value


def _neutralize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    columns: list[str] = []
    for column in normalized.columns:
        base = _neutral_text(column)
        column_option = base
        suffix = 2
        while column_option in columns:
            column_option = f"{base}_{suffix}"
            suffix += 1
        columns.append(column_option)
    normalized.columns = columns
    for column in normalized.select_dtypes(include=["object", "string"]).columns:
        normalized[column] = normalized[column].map(
            lambda value: _neutral_text(value) if isinstance(value, str) else value
        )
    return normalized


def _neutral_text(value: object) -> str:
    text = str(value)
    for term in _FORBIDDEN_TERMS:
        text = re.sub(re.escape(term), "data", text, flags=re.IGNORECASE)
    return text


def _unique_codes(frame: pd.DataFrame) -> list[str]:
    if "code" not in frame:
        raise ValueError("market snapshot has no code column")
    codes = [str(value) for value in frame["code"]]
    if not codes or any(len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError("market snapshot has invalid codes")
    return list(dict.fromkeys(codes))


def _observed_at(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now().astimezone()
    return value.astimezone() if value.tzinfo is None else value


if __name__ == "__main__":
    raise SystemExit(main())

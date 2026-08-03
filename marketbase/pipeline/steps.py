"""流水线步骤 —— 从 local_workflow.py 提取.

各步骤的封装函数：快照采集、日线采集、量比、可执行性、审计分类、分钟快照.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
import math
from pathlib import Path
from typing import Any, cast

import pandas as pd

from marketbase.market_collector import MarketCollectionResult, collect_market_snapshot
from marketbase.daily import fetch_daily_history
from marketbase.daily_collector import (
    DailyCollectionReport,
    DailyProgressEvent,
    collect_daily_universe,
)
from marketbase.daily_collector import _MIN_INDICATOR_ROWS  # pyright: ignore[reportPrivateUsage]
from marketbase.tradability import enrich_tradability
from marketbase.volume_ratio import compute_volume_ratios_batch
from marketbase.intraday import append_minute_snapshot, build_intraday_sequence, audit_minute_sequence, compute_minute_facts

from marketbase.pipeline.helpers import (
    _observed_at,
    _neutral_text,
    _json_value,
    _frame_records,
    _unique_codes,
    _write_json_atomic,
    _INDICATOR_VALUE_FIELDS,
    _INDICATOR_FIELDS,
)
from marketbase.pipeline.progress import _render_bar, _clear_progress_line, _write_progress_line


# ── 市场快照采集 ─────────────────────────────────────────────────────

def _run_market_collection(
    root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
    configured: dict[str, object],
) -> tuple[pd.DataFrame, list[str], MarketCollectionResult]:
    """Acquire market snapshot and return frame, codes, and raw result."""
    cache_root = root / "cache"
    market_cache_path = cache_root / "market_snapshot.json"
    market_collector = cast(Callable[..., MarketCollectionResult], configured.get("market_collector", collect_market_snapshot))
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


def _call_market_collector(
    collector: Callable[..., MarketCollectionResult],
    *,
    cache_path: Path,
    now: datetime,
    progress: Callable[[str], None],
) -> MarketCollectionResult:
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


# ── 日线采集 ─────────────────────────────────────────────────────────

def _indicator_has_value(entry: dict[str, object], fields: tuple[str, ...]) -> bool:
    """Check if any indicator field has a non-None, non-NaN value."""
    for field in fields:
        val = entry.get(field)
        if val is not None and not (isinstance(val, float) and math.isnan(val)):
            return True
    return False


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
    daily_fetcher = cast(Callable[..., pd.DataFrame], configured.get("daily_fetcher", fetch_daily_history))
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
        if _indicator_has_value(entry, _INDICATOR_VALUE_FIELDS)
    )
    indicator_insufficient_count = len(daily_report.indicator_insufficient)
    bse_set = bse_codes or set()
    bse_short = [
        entry for entry in daily_report.short_history
        if str(entry.get("code", "")) in bse_set
    ]
    bse_insufficient = [
        entry for entry in daily_report.indicator_insufficient
        if str(entry.get("code", "")) in bse_set
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
        bse_indicator_empty = sum(
            1 for entry in daily_report.indicators
            if str(entry.get("code", "")) in bse_set
            and not _indicator_has_value(entry, _INDICATOR_VALUE_FIELDS)
        )
        if bse_indicator_empty:
            emit(f"指标全部为空 {indicator_count - indicator_valid_count}（含北交所{bse_indicator_empty}只）")
        else:
            emit(f"指标全部为空 {indicator_count - indicator_valid_count}")
    if daily_report.failure_count:
        emit(f"日线采集失败 {daily_report.failure_count}")
    emit(f"日线耗时{daily_report.elapsed_seconds:.0f}秒")
    rate = (daily_report.total_count / daily_report.elapsed_seconds * 60) if daily_report.elapsed_seconds > 0 else 0
    emit(
        "daily_completed={}/{}".format(cache_valid, daily_report.total_count)
        + " wall_time={}".format(observed_at.isoformat())
        + " elapsed={:.1f}".format(daily_report.elapsed_seconds)
        + " rate={:.0f}/min".format(rate)
        + " eta=0"
        + " cache_hits={}".format(daily_report.cache_hit_count)
        + " failures={}".format(daily_report.failure_count)
        + " pending=0"
        + " current_code="
        + " current_source="
        + " last_error="
    )
    indicators_df = pd.DataFrame(
        daily_report.indicators,
        columns=("code", *_INDICATOR_FIELDS),
    )
    return indicators_df, daily_report


# ── 量比计算 ─────────────────────────────────────────────────────────

def _run_volume_ratio(
    frame: pd.DataFrame,
    cache_root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
) -> pd.DataFrame:
    """Compute volume ratios locally and return updated frame."""
    try:
        frame = compute_volume_ratios_batch(frame, cache_root / "daily", observed_at)
        vr_count = int(cast(pd.Series, frame["volume_ratio"]).notna().sum())
        emit(f"量比本地计算完成 覆盖{vr_count}/{len(frame)}")
    except Exception as exc:
        emit(f"量比本地计算失败: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame


# ── 交易可执行性标注 ─────────────────────────────────────────────────

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
        st_count = int(cast(pd.Series, frame["is_st"]).sum()) if "is_st" in frame.columns else 0
        suspended_count = int(cast(pd.Series, frame["is_suspended"]).sum()) if "is_suspended" in frame.columns else 0
        limit_up_count = int(cast(pd.Series, frame["is_limit_up"]).sum()) if "is_limit_up" in frame.columns else 0
        limit_down_count = int(cast(pd.Series, frame["is_limit_down"]).sum()) if "is_limit_down" in frame.columns else 0
        emit(f"交易可执行性标注: ST={st_count} 停牌={suspended_count} 涨停={limit_up_count} 跌停={limit_down_count}")
    except Exception as exc:
        emit(f"交易可执行性标注失败: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame


# ── 审计与分类 ───────────────────────────────────────────────────────

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
    from marketbase.classify import build_classification_map

    market_audit = audit_market_snapshot(
        frame,
        observed_at=observed_at,
        provider_errors=cast(list[str], result.audit.get("provider_errors", [])),
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
    if "industry" in classification.columns:
        industry_filled = classification["industry"].notna() & (classification["industry"].astype(str).str.strip() != "")
        industry_coverage = float(industry_filled.sum() / max(len(classification), 1))
        industry_missing = int((~industry_filled).sum())
        classification_audit["industry_coverage"] = industry_coverage
        classification_audit["industry_missing_count"] = industry_missing
    return market_audit, classification, classification_audit


def _run_enrich_classification(
    frame: pd.DataFrame, classification: pd.DataFrame, emit: Callable[[str], None]
) -> pd.DataFrame:
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
            frame.loc[missing, col] = (
                frame.loc[missing, col]
                .replace({"": float("nan"), "nan": float("nan"), "None": float("nan"), "<NA>": float("nan")})
                .fillna(fill_values)
            )
            enriched += filled
        if enriched:
            emit(f"class enrichment: {enriched} rows filled")
    except Exception as exc:
        emit(f"class enrichment failed: {_neutral_text(str(exc) or type(exc).__name__)}")
    return frame


# ── 分钟快照追加 ─────────────────────────────────────────────────────

def _run_minute_snapshot(
    frame: pd.DataFrame,
    cache_root: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
    *,
    all_codes: list[str] | None = None,
) -> dict[str, object]:
    """追加分钟快照到 intraday_1m.parquet，计算 VWAP 覆盖，返回审计结果."""
    minute_audit: dict[str, object] = {"status": "pending"}
    from importlib.util import find_spec
    if find_spec("pyarrow") is None and find_spec("fastparquet") is None:
        minute_audit["status"] = "failed"
        minute_audit["append_error"] = (
            "missing parquet engine: install pyarrow (pip install pyarrow) or fastparquet"
        )
        emit(f"minute append failed: {minute_audit['append_error']}")
        return minute_audit
    intraday_path = cache_root / "intraday_1m.parquet"
    try:
        append_minute_snapshot(frame, intraday_path)
        emit("minutes appended to intraday_1m.parquet")
    except Exception as exc:
        minute_audit["status"] = "failed"
        minute_audit["append_error"] = _neutral_text(str(exc) or type(exc).__name__)
        emit(f"minute append failed: {minute_audit['append_error']}")
        return minute_audit
    try:
        seq = build_intraday_sequence(intraday_path)
        if not seq.empty:
            target_date = observed_at.date()
            if "time" in seq.columns:
                seq_time = pd.to_datetime(seq["time"])
                seq = seq[seq_time.dt.date == target_date].copy()
            if seq.empty:
                minute_audit["sequence_error"] = f"no minute data for {target_date}"
                emit(f"minute seq: empty for {target_date}")
            else:
                min_audit = audit_minute_sequence(cast(pd.DataFrame, seq), observed_at=observed_at, all_codes=all_codes)
                minute_audit["sequence_audit"] = min_audit
                minute_audit["total_minutes"] = min_audit.get("actual_minutes", 0)
                minute_audit["total_stocks"] = int(cast(int, cast(pd.DataFrame, seq)["code"].nunique()))
                emit(f"minute seq: {minute_audit['total_minutes']}min x {minute_audit['total_stocks']}stocks")
    except Exception as exc:
        minute_audit["sequence_error"] = _neutral_text(str(exc) or type(exc).__name__)
        emit(f"minute sequence build failed: {minute_audit['sequence_error']}")

    # ── VWAP 计算并写入 frame ──
    try:
        # 优先使用行情源提供的均价（若可用），否则由累计成交额/成交量计算
        if "avg_price" in frame.columns:
            vwap = pd.to_numeric(frame["avg_price"], errors="coerce")
            vol = pd.to_numeric(frame["volume"], errors="coerce") if "volume" in frame.columns else pd.Series(float("nan"), index=frame.index)
            amt = pd.to_numeric(frame["amount"], errors="coerce") if "amount" in frame.columns else pd.Series(float("nan"), index=frame.index)
            frame["vwap_source"] = "market_provided"
        elif "average" in frame.columns:
            vwap = pd.to_numeric(frame["average"], errors="coerce")
            vol = pd.to_numeric(frame["volume"], errors="coerce") if "volume" in frame.columns else pd.Series(float("nan"), index=frame.index)
            amt = pd.to_numeric(frame["amount"], errors="coerce") if "amount" in frame.columns else pd.Series(float("nan"), index=frame.index)
            frame["vwap_source"] = "market_provided"
        elif "amount" in frame.columns and "volume" in frame.columns:
            vol = pd.to_numeric(frame["volume"], errors="coerce")
            amt = pd.to_numeric(frame["amount"], errors="coerce")
            vwap = amt / vol.replace(0, float("nan"))
            frame["vwap_source"] = "computed_from_cumulative"
        else:
            minute_audit["vwap_error"] = "no amount/volume or avg_price columns for VWAP"
            emit(f"VWAP skipped: {minute_audit['vwap_error']}")
            return minute_audit  # 跳过后续 VWAP 写入，但不影响分钟事实
        frame["vwap"] = vwap
        frame["vwap_distance_pct"] = ((pd.to_numeric(frame["price"], errors="coerce") - vwap) / vwap.replace(0, float("nan"))) * 100
        frame["cumulative_volume"] = vol
        frame["cumulative_amount"] = amt
        # 记录单位换算依据：amount 单位为 CNY(元)，volume 单位为 shares(股)
        frame["vwap_unit_note"] = "amount_CNY / volume_shares"
        minute_audit["vwap_coverage"] = int(vwap.notna().sum())
        minute_audit["vwap_total"] = len(frame)
        emit(f"VWAP written to snapshot: {minute_audit['vwap_coverage']}/{len(frame)} computable (source={frame['vwap_source'].iloc[0] if len(frame) > 0 else 'unknown'})")
    except Exception as exc:
        minute_audit["vwap_error"] = _neutral_text(str(exc) or type(exc).__name__)
        emit(f"VWAP write failed: {minute_audit['vwap_error']}")

    if minute_audit.get("status") != "failed":
        minute_audit["status"] = "collected"

    # ── 分钟客观事实字段 ──
    try:
        # 优先使用 intraday_minutes.parquet（完整 OHLCV，含 high/low）
        ohlcv_path = cache_root / "intraday_minutes.parquet"
        if ohlcv_path.exists():
            ohlcv_df = pd.read_parquet(ohlcv_path)
            # 列名映射: timestamp→time, close→price
            ohlcv_df = ohlcv_df.rename(columns={"timestamp": "time", "close": "price"})
            facts_df = compute_minute_facts(cast(pd.DataFrame, ohlcv_df), frame)
            minute_audit["fact_source"] = "intraday_minutes.parquet"
        else:
            seq = build_intraday_sequence(intraday_path)
            if not seq.empty:
                facts_df = compute_minute_facts(cast(pd.DataFrame, seq), frame)
                minute_audit["fact_source"] = "intraday_1m.parquet"
            else:
                facts_df = frame
        fact_cols = [
            "afternoon_pivot", "distance_to_day_high_pct",
            "vol_last_3m", "vol_last_5m", "vol_change_3m_pct",
            "is_limit_touched", "last_tradable_time",
        ]
        for col in fact_cols:
            if col in facts_df.columns:
                frame[col] = facts_df[col].values
        minute_audit["minute_facts_applied"] = True
        fact_count = sum(1 for col in fact_cols if col in facts_df.columns)
        emit(f"minute facts applied: {fact_count} columns (source={minute_audit.get('fact_source', 'unknown')})")
    except Exception as exc:
        minute_audit["minute_facts_error"] = _neutral_text(str(exc) or type(exc).__name__)
        emit(f"minute facts failed: {minute_audit['minute_facts_error']}")

    return minute_audit
"""客观数据本地采集入口与请求补数命令行。

采集全市场快照、250 日日线、中性指标、分类映射和审计报告。
输出纯数据交接文件，不包含任何策略分、排名、推荐或交易结论。

流水线各步骤已解耦到 marketbase.pipeline 子包：
  - pipeline.helpers:   通用工具函数
  - pipeline.progress:  进度条渲染
  - pipeline.steps:     流水线各步骤
  - pipeline.quality:   数据质量评估
  - pipeline.output:    产物写入与 manifest
  - pipeline.index_module: 指数数据采集
  - pipeline.industry:  行业聚合
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import subprocess
import sys
from pathlib import Path
from typing import IO, cast

import pandas as pd  # pyright: ignore[reportMissingTypeStubs]

from marketbase.classification_collector import collect_classification
from marketbase.daily import fetch_daily_history
from marketbase.daily_collector import DailyCollectionReport
from marketbase.data_request import load_data_request, write_data_response
from marketbase.market_collector import MarketCollectionResult, collect_market_snapshot
from marketbase.minute_collector import collect_requested_data
from marketbase.security_master import collect_security_master
from marketbase.market_breadth import compute_market_breadth, compute_industry_ma_distribution
from marketbase.indicators import compute_rps20

from marketbase.pipeline.helpers import (
    _observed_at,
    _neutral_text,
    _create_run_directory,
    _detect_session_slug,
    _write_json_atomic,
    _unique_codes,
    _try_lock_nonblocking,
    _unlock_file,
)
from marketbase.pipeline.progress import _ts, _clear_progress_line
from marketbase.pipeline.steps import (
    _run_market_collection,
    _run_daily_collection,
    _run_volume_ratio,
    _run_tradability,
    _run_audit_and_classification,
    _run_enrich_classification,
    _run_minute_snapshot,
)
from marketbase.pipeline.output import _write_outputs_and_manifest
from marketbase.intraday_collector import collect_intraday_minutes


# ── 主入口 ────────────────────────────────────────────────────────────

def _notify_windows(title: str, message: str) -> None:
    """Windows toast notification via PowerShell."""
    try:
        subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;'
                f'$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
                f'$template.GetElementsByTagName("text")[0].AppendChild($template.CreateTextNode("{title}")) > $null;'
                f'$template.GetElementsByTagName("text")[1].AppendChild($template.CreateTextNode("{message}")) > $null;'
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($template);'
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("MarketBase").Show($toast)',
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except Exception:
        pass  # 通知非关键，静默失败


def run_collection(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    progress: Callable[[str], None] = print,
    providers: Mapping[str, object] | None = None,
    phase: str | None = None,
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
    _ = lock_handle.seek(0)
    acquired = _try_lock_nonblocking(lock_handle)
    if not acquired:
        lock_handle.close()
        print("采集已在运行中（检测到 .workflow.lock），第二个实例退出。", flush=True)
        sys.exit(0)
    try:
        try:
            result = _run_collection_locked(root, observed_at, configured, progress, phase=phase, force_refresh=force_refresh)
            _notify_windows("MarketBase 采集完成", f"运行目录: {result.get('run_dir', 'N/A')}")
            return result
        except Exception:
            _notify_windows("MarketBase 采集失败", "请查看 workflow.log 了解详情")
            raise
    finally:
        _unlock_file(lock_handle)
        lock_handle.close()


def _run_collection_locked(
    root: Path,
    observed_at: datetime,
    configured: dict[str, object],
    _progress: Callable[[str], None],
    phase: str | None = None,
    force_refresh: bool = False,
) -> dict[str, object]:
    """持有文件锁后执行核心采集流程：快照 → 日线 → 指标 → 量比 → 审计 → 分类 → 交接."""
    collection_started_at = datetime.now().astimezone().isoformat()
    session_phase = _detect_session_slug(observed_at, phase)
    run_dir = _create_run_directory(root, observed_at, phase=session_phase)
    log_path = run_dir / "workflow.log"

    def emit(message: str) -> None:
        _clear_progress_line()
        text = f"{_ts()}  {_neutral_text(message)}"
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(text + "\n")

    emit("开始采集")
    cache_root = root / "cache"

    # ① 实时行情快照
    frame, codes, result = _run_market_collection(root, observed_at, emit, configured)
    bse_codes = set(frame.loc[frame["market"] == "bj", "code"].tolist()) if "market" in frame.columns else set()
    # ①.5 完整 1 分钟 OHLCV 采集（盘中下午时段）—— 必须在分钟快照之前，确保分钟事实使用本次新采集的完整 parquet
    intraday_minutes_audit, intraday_minutes_path = _run_intraday_minutes_collection(
        codes, cache_root, run_dir, observed_at, emit, session_phase
    )
    # ①.6 分钟快照追加与 VWAP 计算（优先读取 intraday_minutes.parquet）
    minute_audit = _run_minute_snapshot(frame, cache_root, observed_at, emit, all_codes=codes)
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
    full_market = cast(dict[str, int], breadth.get("full_market", {}))
    emit(f"市场广度: 涨{full_market.get('advance_count', 0)} "
         + f"跌{full_market.get('decline_count', 0)} "
         + f"平{full_market.get('unchanged_count', 0)}")

    # --- 行业MA分布 ---
    ind_ma = compute_industry_ma_distribution(frame, indicators_df)
    _write_json_atomic(run_dir / "industry_ma_distribution.json", ind_ma)
    emit(f"行业MA分布: {len(ind_ma)} 行业")

    summary = _write_outputs_and_manifest(
        run_dir, root, observed_at, frame, indicators_df, classification,
        market_audit, classification_audit, result, daily_report, codes,
        minute_audit, collection_started_at, emit, cache_root, phase=phase,
        intraday_minutes_audit=intraday_minutes_audit,
        intraday_minutes_path=intraday_minutes_path,
    )
    # 写入分钟审计（已合并到 data_audit.json，不再单独写）
    return summary


def _run_intraday_minutes_collection(
    codes: list[str],
    cache_root: Path,
    run_dir: Path,
    observed_at: datetime,
    emit: Callable[[str], None],
    session_phase: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Collect full 1-minute OHLCV data for all stocks during afternoon session.

    Only runs during intraday afternoon phases (intraday_1300, intraday_1400, intraday_1430).
    Writes to cache_root/intraday_minutes.parquet and copies to run_dir.
    Returns (audit, copied_path).
    """
    # 仅下午盘中时段执行
    if not session_phase.startswith("intraday") or session_phase == "intraday_morning":
        emit("intraday OHLCV: skipped (not afternoon session)")
        return None, None

    try:
        target_date = observed_at.astimezone().date().isoformat()
        output_path = cache_root / "intraday_minutes.parquet"
        audit = collect_intraday_minutes(
            codes,
            output_path,
            target_date=target_date,
            start_time="13:00",
            batch_size=30,
            batch_interval=0.3,
            max_workers=4,
            observed_at=observed_at,
            progress=emit,
        )
        emit(f"intraday OHLCV: {audit.get('actual_minutes', 0)}min x {audit.get('codes_with_data', 0)}stocks")
        # 复制到 run_dir
        import shutil
        dest_path = run_dir / "intraday_minutes.parquet"
        shutil.copy2(str(output_path), str(dest_path))
        emit("intraday OHLCV copied to run_dir")
        return audit, str(dest_path)
    except Exception as exc:
        emit(f"intraday OHLCV failed: {_neutral_text(str(exc) or type(exc).__name__)}")
        return {"status": "failed", "error": str(exc)}, None


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
    payload = collect_requested_data(request, **collection_args)  # pyright: ignore[reportArgumentType]
    _ = write_data_response(response_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：默认全量采集，也支持 collect-classify / refresh-master / fulfill-request 子命令."""
    parser = argparse.ArgumentParser(description="MarketBase 客观数据入口")
    _ = parser.add_argument("--data-root", type=Path)
    _ = parser.add_argument("--phase", type=str, default="post_close",
                        help="采集阶段: 留空则根据观测时间自动判定 (intraday_morning | intraday_1300 | intraday_1400 | intraday_1430 | post_close)")
    _ = parser.add_argument("--force-refresh", action="store_true", default=False,
                        help="强制重新拉取日线数据，忽略缓存")
    subcommands = parser.add_subparsers(dest="command")
    _ = subcommands.add_parser("collect")
    request_parser = subcommands.add_parser("fulfill-request")
    _ = request_parser.add_argument("--request", type=Path)
    _ = request_parser.add_argument("--response", type=Path)
    classify_parser = subcommands.add_parser("collect-classify")
    _ = classify_parser.add_argument("--output", type=Path)
    master_parser = subcommands.add_parser("refresh-master")
    _ = master_parser.add_argument("--output", type=Path)
    t1_parser = subcommands.add_parser("t1-analyze")
    _ = t1_parser.add_argument("--watchlist", type=Path)
    _ = t1_parser.add_argument("--output", type=Path)
    _ = t1_parser.add_argument("--v2", action="store_true", help="使用 V2 策略全生命周期模块")
    t1_snap_parser = subcommands.add_parser("build-t1-snapshot")
    _ = t1_snap_parser.add_argument("--watchlist", type=Path)
    _ = t1_snap_parser.add_argument("--output", type=Path)
    _ = t1_snap_parser.add_argument("--v2", action="store_true", help="使用 V2 策略全生命周期模块")
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
            _ = fulfill_request(
                request_path=arguments.request or root / "codex_data_request.json",
                response_path=arguments.response or root / "codex_data_response.json",
                data_root=root,
            )
            print("客观数据请求补数完成")
            return 0
        if arguments.command == "t1-analyze":
            from strategies.t1_snapshot import build_t1_snapshot, build_t1_snapshot_v2
            from strategies.t1_analysis import run_analysis
            root = arguments.data_root or default_root
            use_v2 = getattr(arguments, "v2", False)
            if use_v2:
                result = build_t1_snapshot_v2(
                    data_root=root,
                    watchlist_path=arguments.watchlist,
                    output_path=arguments.output,
                )
            else:
                result = build_t1_snapshot(
                    data_root=root,
                    watchlist_path=arguments.watchlist,
                    output_path=arguments.output,
                )
            if result != 0:
                return result
            return run_analysis(
                data_root=root,
                input_path=arguments.output,
                output_path=None,
                use_v2=use_v2,
            )
        if arguments.command == "build-t1-snapshot":
            from strategies.t1_snapshot import build_t1_snapshot, build_t1_snapshot_v2
            root = arguments.data_root or default_root
            use_v2 = getattr(arguments, "v2", False)
            if use_v2:
                return build_t1_snapshot_v2(
                    data_root=root,
                    watchlist_path=arguments.watchlist,
                    output_path=arguments.output,
                )
            else:
                return build_t1_snapshot(
                    data_root=root,
                    watchlist_path=arguments.watchlist,
                    output_path=arguments.output,
                )
        summary = run_collection(
            data_root=getattr(arguments, "data_root", None) or default_root,
            phase=getattr(arguments, "phase", "post_close"),
            force_refresh=getattr(arguments, "force_refresh", False),
        )
        print(
            "客观数据采集完成: "
            + f"市场行数={summary['market_rows']} "
            + f"日线成功={summary['daily_success']} 日线失败={summary['daily_failure']}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - command boundary prints one neutral failure.
        print(f"数据采集错误: {_neutral_text(str(exc) or type(exc).__name__)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
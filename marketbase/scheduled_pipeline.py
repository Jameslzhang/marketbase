"""Run collection, fixed handoffs, scan, decision and report as one bounded job."""
from __future__ import annotations

import argparse
from datetime import datetime, time
import json
from pathlib import Path
import sys

import pandas as pd

from marketbase.scheduled_workflow import CN, ROOT, _save, preflight, run_stage
from marketbase.intraday_collector import collect_intraday_minutes
from marketbase.plan_lifecycle import freeze_plan_bundle, review_frozen_plans


def _find_prior_plans(
    data_root: Path, trade_date: str, *, before_run: Path,
    source_slots: tuple[str, ...] = ("1130",),
) -> list[Path]:
    """Return the newest valid frozen bundle for every requested source slot."""
    day = data_root / "scheduled" / trade_date
    if not day.exists():
        return []
    found: list[Path] = []
    for source_slot in source_slots:
        candidates = [
            path / "plan.json"
            for path in day.iterdir()
            if path.is_dir() and path != before_run and path.name.startswith(f"{source_slot}_")
            and (path / "plan.json").is_file()
        ]
        for path in sorted(candidates, key=lambda item: item.parent.name, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("trade_date") == trade_date and payload.get("slot") == source_slot:
                found.append(path)
                break
    return found


def _find_prior_plan(
    data_root: Path, trade_date: str, *, before_run: Path,
    source_slots: tuple[str, ...] = ("1130",),
) -> Path | None:
    """Compatibility helper for callers that need only the highest-priority bundle."""
    plans = _find_prior_plans(data_root, trade_date, before_run=before_run, source_slots=source_slots)
    return plans[0] if plans else None


def _collection_command(handoff: Path, data_root: Path, slot: str | None) -> list[str]:
    command = [
        sys.executable, str(ROOT / "local_workflow.py"),
        "--data-root", str(data_root),
    ]
    if slot != "1530":
        command.append("--daily-cache-only")
    if slot in {"0945", "1015", "1130", "1345", "1430"}:
        command.append("--defer-intraday-minutes")
    command.extend(["collect", "--handoff-output", str(handoff)])
    return command


def _load_minutes(handoff: dict) -> pd.DataFrame:
    raw_path = handoff.get("intraday_minutes_path")
    if not raw_path:
        return pd.DataFrame()
    path = Path(str(raw_path))
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError, ImportError):
        return pd.DataFrame()


def _candidate_codes(candidate_path: Path, *, limit: int = 12) -> list[str]:
    """Keep minute requests bounded and limited to the already-selected leaders."""
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    codes: list[str] = []
    for row in payload.get("candidates", []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", "")).strip().zfill(6)
        if len(code) == 6 and code.isdigit() and code not in codes:
            codes.append(code)
        if len(codes) >= limit:
            break
    return codes


def _refresh_candidate_prices(candidate_path: Path, minute_path: Path) -> dict[str, object]:
    """Replace scan-time candidate prices with the newest collected minute close.

    The scan discovers candidates.  Execution must use the later candidate-only
    collection, otherwise a stock can leave its buy zone while the pipeline is
    still preparing the report.
    """
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        raise ValueError("candidate payload candidates must be a list")

    minutes = pd.read_parquet(minute_path)
    required = {"code", "timestamp", "close"}
    if minutes.empty or not required.issubset(minutes.columns):
        return {
            "status": "no_usable_prices",
            "updated_codes": [],
            "missing_codes": _candidate_codes(candidate_path),
        }
    working = minutes.loc[:, ["code", "timestamp", "close"]].copy()
    working["code"] = working["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    working["timestamp"] = pd.to_datetime(working["timestamp"], errors="coerce")
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    working = working.dropna(subset=["timestamp", "close"])
    working = working[working["close"].gt(0)].sort_values("timestamp")
    latest = {
        str(row.code): row
        for row in working.groupby("code", sort=False).tail(1).itertuples(index=False)
    }

    updated: list[str] = []
    missing: list[str] = []
    observed: list[pd.Timestamp] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", "")).strip().zfill(6)
        minute = latest.get(code)
        if minute is None:
            missing.append(code)
            continue
        row.setdefault("scan_price", row.get("price"))
        row["price"] = round(float(minute.close), 4)
        row["price_source"] = "candidate_minutes_last_close"
        stamp = pd.Timestamp(minute.timestamp)
        row["price_observed_at"] = stamp.isoformat()
        observed.append(stamp)
        updated.append(code)

    if observed:
        payload["scan_observed_at"] = payload.get("observed_at")
        payload["observed_at"] = max(observed).isoformat()
    payload["execution_price_refresh"] = {
        "status": "refreshed" if updated else "no_usable_prices",
        "updated_codes": updated,
        "missing_codes": missing,
        "source": str(minute_path.resolve()),
    }
    _save(candidate_path, payload)
    return payload["execution_price_refresh"]


def _collect_candidate_minutes(
    *, handoff_path: Path, candidate_path: Path, run_dir: Path, now: datetime,
) -> dict[str, object]:
    """Attach same-round minute evidence only for post-screening candidates.

    The full-market scan remains complete.  This bounded follow-up only changes
    the execution evidence scope, so absent or failed minute data still closes
    execution in the decision layer.
    """
    local_now = now.astimezone(CN)
    if not time(9, 30) <= local_now.time() < time(15, 0):
        return {"status": "not_requested", "reason": "outside_trading_session"}
    if time(11, 30) <= local_now.time() < time(13, 0):
        return {"status": "not_requested", "reason": "lunch_break"}
    codes = _candidate_codes(candidate_path)
    if not codes:
        return {"status": "not_requested", "reason": "no_screened_candidates"}

    start_time = "09:30" if local_now.time() < time(13, 0) else "13:00"
    minute_path = run_dir / "candidate_intraday_minutes.parquet"
    audit = collect_intraday_minutes(
        codes, minute_path, target_date=local_now.date().isoformat(), start_time=start_time,
        batch_size=len(codes), batch_interval=0.0, max_workers=min(8, len(codes)),
        resume=False, observed_at=local_now, session_phase="candidate_only",
    )
    audit = {**audit, "scope": "screened_candidates", "requested_codes": codes}
    _save(run_dir / "candidate_minutes_audit.json", audit)
    if not minute_path.is_file() or not audit.get("codes_with_data"):
        return {**audit, "status": "failed"}

    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    source_audit = Path(str(handoff["data_audit_path"]))
    data_audit = json.loads(source_audit.read_text(encoding="utf-8"))
    old_reasons = [str(item) for item in data_audit.get("quality_reason_codes", [])]
    data_audit.update(
        quality_status="partial",
        quality_reason_codes=list(dict.fromkeys(["candidate_minutes_only", *old_reasons])),
        intraday_minutes=audit,
        minute_continuity={
            "status": "collected", "scope": "screened_candidates",
            "actual_minutes": int(audit.get("actual_minutes", 0)),
            "total_stocks": int(audit.get("codes_with_data", 0)),
        },
    )
    revised_audit = run_dir / "data_audit_candidate_minutes.json"
    _save(revised_audit, data_audit)
    handoff["intraday_minutes_path"] = str(minute_path.resolve())
    handoff["data_audit_path"] = str(revised_audit.resolve())
    handoff["minute_evidence_scope"] = "screened_candidates"
    _save(handoff_path, handoff)
    price_refresh = _refresh_candidate_prices(candidate_path, minute_path)
    return {
        **audit,
        "status": "collected",
        "path": str(minute_path),
        "price_refresh": price_refresh,
    }


def run_pipeline(run_dir: Path, report_path: Path, *, data_root: Path | None = None,
                 collection_timeout: float = 900, scan_timeout: float = 180,
                 decision_timeout: float = 90, slot: str | None = None) -> dict:
    run_dir = run_dir.resolve()
    report_path = report_path.resolve()
    if report_path.exists():
        raise FileExistsError(report_path)
    run_dir.mkdir(parents=True, exist_ok=False)
    data_root = (data_root or ROOT / "data/daily_runs").resolve()
    started = datetime.now(CN)
    result = {"status": "running", "started_at": started.isoformat(), "steps": {},
              "run_dir": str(run_dir), "report_path": str(report_path),
              "delivery_status": "pending", "only_choose_one": None}
    step = "preflight"
    body = ""
    try:
        check = preflight(data_root / "cache/trade_calendar.csv", [data_root, report_path.parent])
        result["preflight"] = check
        if check["status"] == "non_trading_day":
            result["status"] = "non_trading_day"
            _save(run_dir / "result.json", result)
            return result
        if check["status"] != "ready":
            raise RuntimeError(json.dumps(check, ensure_ascii=False))

        handoff = run_dir / "handoff.json"
        candidates = run_dir / "candidates.json"
        decision_path = run_dir / "decision.json"
        stages_dir = run_dir / "stages"
        stages_dir.mkdir()

        def execute(name: str, command: list[str], timeout: float) -> None:
            nonlocal step
            step = name
            outcome = run_stage(command, stages_dir / f"{name}.json", timeout, ROOT)
            result["steps"][name] = outcome
            _save(run_dir / "result.json", result)
            if outcome["status"] != "stage_succeeded":
                raise RuntimeError(json.dumps(outcome, ensure_ascii=False))

        effective_slot = slot or (run_dir.name.split("_", 1)[0] if "_" in run_dir.name else None)
        execute("collection", _collection_command(handoff, data_root, slot), collection_timeout)
        fixed = json.loads(handoff.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(fixed["generated_at"])
        if observed.tzinfo is None or observed.astimezone(CN).date() != started.date():
            raise ValueError("handoff date does not match this run")
        result["observed_at"] = observed.isoformat()
        execute("scan", [sys.executable, str(ROOT / "fast_t1_scan.py"), "--fresh", "0",
            "--data-root", str(data_root.parent), "--candidate-output", str(candidates),
            "--html", str(run_dir / "scan.html")], scan_timeout)
        json.loads(candidates.read_text(encoding="utf-8"))
        if effective_slot in {"0945", "1015", "1130", "1345", "1430"}:
            # Candidate-minute failures are execution evidence failures, not a
            # reason to suppress the research result.  The decision layer
            # receives no minute path on failure and therefore fails closed.
            step = "candidate_minutes"
            try:
                minute_outcome = _collect_candidate_minutes(
                    handoff_path=handoff, candidate_path=candidates, run_dir=run_dir, now=datetime.now(CN)
                )
            except Exception as exc:  # noqa: BLE001 - preserve a deliverable failure result.
                minute_outcome = {"status": "failed", "error": str(exc)}
            result["steps"][step] = minute_outcome
            _save(run_dir / "result.json", result)
            fixed = json.loads(handoff.read_text(encoding="utf-8"))
        execute("decision", [sys.executable, str(ROOT / "local_workflow.py"),
            "--data-root", str(data_root), "full-market-t1", "--candidate-union", str(candidates),
            "--handoff", str(handoff), "--decision-at", datetime.now(CN).isoformat(),
            "--output", str(decision_path)], decision_timeout)
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        for field in ["research_status", "execution_status", "pipeline_status", "only_choose_one"]:
            if field not in decision:
                raise ValueError(f"decision missing field: {field}")
        if effective_slot in {"0945", "1015", "1130", "1345", "1430", "1530"}:
            from local_workflow import format_decision_result

            published_at = datetime.now(CN)
            plan_bundle = freeze_plan_bundle(
                decision, slot=effective_slot, published_at=published_at, source_path=decision_path
            )
            plan_path = run_dir / "plan.json"
            _save(plan_path, plan_bundle)
            decision["plan_bundle"] = plan_bundle
            decision["plan_status"] = plan_bundle["plan_status"]
            result.update(plan_path=str(plan_path), plan_status=plan_bundle["plan_status"])

            review_sources = {
                "1015": ("0945",),
                "1130": ("0945",),
                "1345": ("1130", "0945"),
                "1430": ("1130", "0945"),
                "1530": ("1130", "0945"),
            }
            if effective_slot in review_sources:
                trade_date = str(decision.get("trade_date") or started.date().isoformat())
                prior_paths = _find_prior_plans(
                    data_root, trade_date, before_run=run_dir,
                    source_slots=review_sources[effective_slot],
                )
                if prior_paths:
                    prior_bundles = [json.loads(path.read_text(encoding="utf-8")) for path in prior_paths]
                    reviews = [
                        review
                        for bundle in prior_bundles
                        for review in review_frozen_plans(
                            bundle, decision, _load_minutes(fixed), reviewed_at=published_at
                        )
                    ]
                    decision["prior_plan_source"] = str(prior_paths[0])
                    decision["prior_plan_sources"] = [str(path) for path in prior_paths]
                    decision["prior_plan_slot"] = prior_bundles[0].get("slot")
                    decision["prior_plan_review_status"] = "reviewed"
                    decision["prior_plan_reviews"] = reviews
                else:
                    decision["prior_plan_source"] = None
                    decision["prior_plan_sources"] = []
                    decision["prior_plan_slot"] = review_sources[effective_slot][0]
                    decision["prior_plan_review_status"] = "missing"
                    decision["prior_plan_reviews"] = []
            _save(decision_path, decision)
            body = format_decision_result(decision)
            decision_path.with_name("decision_结果正文.md").write_text(body, encoding="utf-8")
        else:
            body = decision_path.with_name("decision_结果正文.md").read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError("decision result body is empty")
        result.update(status="report_ready", decision_path=str(decision_path),
                      research_status=decision["research_status"], execution_status=decision["execution_status"],
                      pipeline_status=decision["pipeline_status"], only_choose_one=decision["only_choose_one"])
    except Exception as exc:
        result.update(status="pipeline_error", failed_step=step, error=str(exc),
                      research_status="not_generated", execution_status="blocked", pipeline_status="pipeline_error")
        body = (f"# 定时分析运行失败\n\n研究首选：未生成；研究前三：未生成。\n\n"
                f"执行首选：暂无；only_choose_one=null。\n\n"
                f"研究状态：not_generated；执行状态：blocked；程序状态：pipeline_error。\n\n"
                f"失败步骤：{step}\n\n错误：{exc}\n\n"
                f"阶段记录和原始日志：{run_dir}\n\n失败不能解释为全市场没有机会。\n")
        log = run_dir / "stages" / f"{step}.log"
        if log.exists():
            body += "\n日志末尾：\n```text\n" + log.read_text(encoding="utf-8", errors="replace")[-3000:] + "\n```\n"
    result["completed_at"] = datetime.now(CN).isoformat()
    body += f"\n\n实际运行开始：{result['started_at']}；结束：{result['completed_at']}。\n"
    (run_dir / "result.md").write_text(body, encoding="utf-8")
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as handle:
            handle.write(body)
        result["report_status"] = "written"
    except OSError as exc:
        result.update(report_status="failed", report_error=str(exc))
    _save(run_dir / "result.json", result)
    print("结果正文开始\n" + body + "\n结果正文结束")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", choices=["0945", "1015", "1130", "1345", "1430", "1530"], required=True)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data/daily_runs")
    parser.add_argument("--collection-timeout", type=float, default=900)
    parser.add_argument("--scan-timeout", type=float, default=180)
    parser.add_argument("--decision-timeout", type=float, default=90)
    args = parser.parse_args()
    now = datetime.now(CN)
    run = args.data_root / "scheduled" / now.strftime("%Y-%m-%d") / f"{args.slot}_{now:%H%M%S_%f}"
    report = Path("D:/Codex/02_正式交付区") / f"V{now:%m%d%H%M}_A股定时分析{args.slot}.md"
    version = 2
    while report.exists():
        report = report.with_name(f"V{now:%m%d%H%M}_A股定时分析{args.slot}_第{version}版.md")
        version += 1
    try:
        result = run_pipeline(run, report, data_root=args.data_root,
            collection_timeout=args.collection_timeout, scan_timeout=args.scan_timeout,
            decision_timeout=args.decision_timeout, slot=args.slot)
    except Exception as exc:
        result = {"status": "pipeline_error", "error": str(exc), "delivery_status": "pending"}
        print(f"研究首选：未生成；研究前三：未生成；执行首选：暂无。运行入口失败：{exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"report_ready", "non_trading_day"} and result.get("report_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

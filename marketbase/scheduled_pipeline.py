"""Run collection, fixed handoffs, scan, decision and report as one bounded job."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from marketbase.scheduled_workflow import CN, ROOT, _save, preflight, run_stage


def run_pipeline(run_dir: Path, report_path: Path, *, data_root: Path | None = None,
                 collection_timeout: float = 900, scan_timeout: float = 180,
                 decision_timeout: float = 90) -> dict:
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

        execute("collection", [sys.executable, str(ROOT / "local_workflow.py"),
            "--data-root", str(data_root), "collect", "--handoff-output", str(handoff)], collection_timeout)
        fixed = json.loads(handoff.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(fixed["generated_at"])
        if observed.tzinfo is None or observed.astimezone(CN).date() != started.date():
            raise ValueError("handoff date does not match this run")
        result["observed_at"] = observed.isoformat()
        execute("scan", [sys.executable, str(ROOT / "fast_t1_scan.py"), "--fresh", "0",
            "--data-root", str(data_root.parent), "--candidate-output", str(candidates),
            "--html", str(run_dir / "scan.html")], scan_timeout)
        json.loads(candidates.read_text(encoding="utf-8"))
        execute("decision", [sys.executable, str(ROOT / "local_workflow.py"),
            "--data-root", str(data_root), "full-market-t1", "--candidate-union", str(candidates),
            "--handoff", str(handoff), "--decision-at", datetime.now(CN).isoformat(),
            "--output", str(decision_path)], decision_timeout)
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        for field in ["research_status", "execution_status", "pipeline_status", "only_choose_one"]:
            if field not in decision:
                raise ValueError(f"decision missing field: {field}")
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
    parser.add_argument("--slot", choices=["1130", "1345", "1430", "1530"], required=True)
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
            decision_timeout=args.decision_timeout)
    except Exception as exc:
        result = {"status": "pipeline_error", "error": str(exc), "delivery_status": "pending"}
        print(f"研究首选：未生成；研究前三：未生成；执行首选：暂无。运行入口失败：{exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"report_ready", "non_trading_day"} and result.get("report_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

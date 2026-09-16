"""Scheduled-run guards. No market inference and no weekday fallback."""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CN = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]


def preflight(calendar: Path, writable: list[Path], now: datetime | None = None) -> dict:
    now = now or datetime.now(CN)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(CN)
    result = {"observed_at": now.isoformat(), "trade_date": now.date().isoformat(),
              "calendar_path": str(calendar), "status": "ready", "errors": []}
    try:
        with calendar.open(encoding="utf-8-sig", newline="") as handle:
            dates = {date.fromisoformat(row["trade_date"]) for row in csv.DictReader(handle)}
        if not dates or not min(dates) <= now.date() <= max(dates):
            raise ValueError("calendar does not cover current date")
        result["calendar_through"] = max(dates).isoformat()
    except (OSError, ValueError, KeyError) as exc:
        result.update(status="calendar_unavailable", errors=[str(exc)])
        return result
    if now.date() not in dates:
        result["status"] = "non_trading_day"
        return result
    for directory in writable:
        probe = directory / f".schedule-probe-{uuid.uuid4().hex}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with probe.open("x", encoding="utf-8") as handle:
                handle.write("permission probe")
            probe.unlink()
        except OSError as exc:
            result["status"] = "permission_blocked"
            result["errors"].append(f"{directory}: {exc}")
    return result


def _save(path: Path, result: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_stage(command: list[str], state: Path, timeout: float, cwd: Path) -> dict:
    if timeout <= 0 or not command:
        raise ValueError("positive timeout and command required")
    state.parent.mkdir(parents=True, exist_ok=True)
    result = {"status": "running", "started_at": datetime.now(CN).isoformat(),
              "command": command, "cwd": str(cwd), "timeout_seconds": timeout,
              "exit_code": None, "delivery_status": "pending", "only_choose_one": None,
              "log_path": str(state.with_suffix(".log"))}
    _save(state, result)
    start = time.monotonic()
    try:
        with state.with_suffix(".log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                                       start_new_session=os.name != "nt")
            result["pid"] = process.pid
            _save(state, result)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill only this stage's tree, including Windows venv launcher children.
                if os.name == "nt":
                    stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                             stdout=log, stderr=subprocess.STDOUT, timeout=10, check=False)
                    if stopped.returncode != 0:
                        result["termination_warning"] = "Process-tree termination could not be confirmed"
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                raise
        result["exit_code"] = returncode
        result["status"] = "stage_succeeded" if returncode == 0 else "pipeline_error"
    except subprocess.TimeoutExpired:
        result.update(status="pipeline_timeout", error=f"Stage exceeded {timeout} seconds; termination requested")
    except OSError as exc:
        result.update(status="pipeline_error", error=str(exc))
    result.update(completed_at=datetime.now(CN).isoformat(), elapsed_seconds=round(time.monotonic() - start, 3))
    _save(state, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--calendar", type=Path, default=ROOT / "data/daily_runs/cache/trade_calendar.csv")
    check.add_argument("--now", type=datetime.fromisoformat)
    check.add_argument("--writable", type=Path, action="append")
    stage = commands.add_parser("stage")
    stage.add_argument("--state", required=True, type=Path)
    stage.add_argument("--timeout", required=True, type=float)
    stage.add_argument("--cwd", type=Path, default=ROOT)
    stage.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "preflight":
            result = preflight(args.calendar, args.writable or [ROOT / "data/daily_runs", Path("D:/Codex/02_正式交付区")], args.now)
        else:
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            result = run_stage(command, args.state, args.timeout, args.cwd)
    except (OSError, ValueError) as exc:
        result = {"status": "pipeline_error", "error": str(exc), "only_choose_one": None, "delivery_status": "pending"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ready", "non_trading_day", "stage_succeeded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

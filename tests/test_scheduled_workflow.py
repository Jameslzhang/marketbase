import json
import sys
import time
from datetime import datetime

import pytest

from marketbase.scheduled_workflow import preflight, run_stage


@pytest.fixture
def calendar(tmp_path):
    path = tmp_path / "calendar.csv"
    path.write_text("trade_date\n2026-09-11\n2026-09-14\n2026-09-15\n", encoding="utf-8")
    return path


def test_monday_and_timezone(calendar, tmp_path):
    result = preflight(calendar, [tmp_path], datetime.fromisoformat("2026-09-13T23:00:00+00:00"))
    assert result["trade_date"] == "2026-09-14"
    assert result["status"] == "ready"


def test_weekend_and_expired_calendar(calendar, tmp_path):
    assert preflight(calendar, [tmp_path], datetime.fromisoformat("2026-09-13T09:45:00+08:00"))["status"] == "non_trading_day"
    assert preflight(calendar, [tmp_path], datetime.fromisoformat("2026-09-16T09:45:00+08:00"))["status"] == "calendar_unavailable"


def test_missing_calendar_and_permission_error(calendar, tmp_path):
    now = datetime.fromisoformat("2026-09-14T09:45:00+08:00")
    assert preflight(tmp_path / "missing", [tmp_path], now)["status"] == "calendar_unavailable"
    blocked = tmp_path / "file"
    blocked.write_text("unchanged")
    result = preflight(calendar, [blocked], now)
    assert result["status"] == "permission_blocked"
    assert blocked.read_text() == "unchanged"


def test_stage_timeout_and_failure_are_persisted(tmp_path):
    state = tmp_path / "status.json"
    result = run_stage([sys.executable, "-c", "import time; time.sleep(10)"], state, .1, tmp_path)
    assert result["status"] == "pipeline_timeout"
    assert json.loads(state.read_text())["only_choose_one"] is None
    result = run_stage([sys.executable, "-c", "raise SystemExit(7)"], state, 5, tmp_path)
    assert result["exit_code"] == 7
    assert result["status"] == "pipeline_error"


def test_success_is_not_delivery(tmp_path):
    state = tmp_path / "status.json"
    result = run_stage([sys.executable, "-c", "print('done')"], state, 5, tmp_path)
    assert result["status"] == "stage_succeeded"
    assert result["delivery_status"] == "pending"
    assert "done" in (tmp_path / "status.log").read_text()


def test_stage_logs_chinese_as_utf8(tmp_path):
    state = tmp_path / "status.json"
    result = run_stage([sys.executable, "-c", "print('研究首选：未生成')"], state, 5, tmp_path)
    assert result["status"] == "stage_succeeded"
    assert "研究首选：未生成" in state.with_suffix(".log").read_text(encoding="utf-8")


def test_timeout_stops_owned_descendants(tmp_path):
    marker = tmp_path / "leaked.txt"
    child = f"import time,pathlib; time.sleep(1); pathlib.Path({str(marker)!r}).write_text('leaked')"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    run_stage([sys.executable, "-c", parent], tmp_path / "stage.json", .3, tmp_path)
    time.sleep(1.5)
    assert not marker.exists()

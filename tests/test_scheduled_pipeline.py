import json
from datetime import datetime, timezone

import pytest

from marketbase import scheduled_pipeline as pipeline


@pytest.mark.parametrize("failure", [None, "collection", "scan", "decision", "missing_decision"])
def test_pipeline_publishes_success_or_failure_body(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(pipeline, "preflight", lambda *_: {"status": "ready", "trade_date": "2026-09-15"})
    calls = []
    def run(command, state, timeout, cwd):
        step = state.stem
        calls.append(step)
        if step == failure:
            state.with_suffix(".log").write_text("fixture failure", encoding="utf-8")
            return {"status": "pipeline_timeout", "exit_code": None, "command": command}
        if step == "collection":
            path = command[command.index("--handoff-output") + 1]
            from pathlib import Path
            Path(path).write_text(json.dumps({"generated_at": datetime.now(pipeline.CN).isoformat(), "run_dir": str(tmp_path)}))
        if step == "scan":
            from pathlib import Path
            Path(command[command.index("--candidate-output") + 1]).write_text('{"candidates": []}')
        if step == "decision" and failure != "missing_decision":
            from pathlib import Path
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps({"research_status": "data_not_ready", "execution_status": "blocked", "pipeline_status": "completed", "only_choose_one": None}))
            output.with_name(output.stem + "_结果正文.md").write_text("研究首选：未生成\n执行首选：暂无", encoding="utf-8")
        outcome = {"status": "stage_succeeded", "exit_code": 0}
        pipeline._save(state, outcome)
        return outcome
    monkeypatch.setattr(pipeline, "run_stage", run)
    report = tmp_path / "report.md"
    result = pipeline.run_pipeline(tmp_path / "run", report, data_root=tmp_path / "daily_runs")
    assert report.exists()
    assert "执行首选" in report.read_text(encoding="utf-8")
    assert result["delivery_status"] == "pending"
    assert result["status"] == ("report_ready" if failure is None else "pipeline_error")
    if failure in ["collection", "scan", "decision"]:
        assert calls[-1] == failure
        assert failure in report.read_text(encoding="utf-8")


def test_pipeline_does_not_silently_reuse_existing_run(tmp_path):
    run = tmp_path / "existing"
    run.mkdir()
    with pytest.raises(FileExistsError):
        pipeline.run_pipeline(run, tmp_path / "report.md")


def test_calendar_failure_becomes_report_not_nontrading_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "preflight", lambda *_: {"status": "calendar_unavailable", "errors": ["expired"]})
    report = tmp_path / "report.md"
    result = pipeline.run_pipeline(tmp_path / "run", report)
    assert result["status"] == "pipeline_error"
    assert "calendar_unavailable" in report.read_text(encoding="utf-8")

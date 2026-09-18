import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
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


def _decision(code="600000", *, price=10.5, buyable=False):
    return {
        "trade_date": "2026-09-16",
        "observed_at": "2026-09-16T11:39:58+08:00",
        "decision_at": "2026-09-16T11:40:00+08:00",
        "research_status": "ready",
        "execution_status": "ready",
        "pipeline_status": "completed",
        "snapshot_age_seconds": 30.0,
        "only_choose_one": code if buyable else None,
        "research_first_choice": code,
        "research_choices": [{
            "rank": 1, "code": code, "name": "示例", "price": price,
            "opportunity_score": 75.0, "execution_score": 70.0,
            "buyable": buyable, "reason_codes": [],
            "research_reference": {
                "buy_low": 9.8, "buy_high": 10.2,
                "no_chase_price": 10.8, "protect": 9.5,
            },
        }],
        "audit_rows": [{
            "code": code, "name": "示例", "price": price,
            "opportunity_score": 75.0, "execution_score": 70.0,
            "buyable": buyable, "buy_low": 9.8, "buy_high": 10.2,
            "confirm_price": 10.25, "chase_line": 10.8, "protect": 9.5,
            "sell1_low": 10.9, "sell1_high": 11.1,
            "sell2_low": 11.4, "sell2_high": 11.8,
            "reason_codes": [],
        }],
    }


def _stub_pipeline(monkeypatch, tmp_path, decision):
    cn = timezone(timedelta(hours=8))
    monkeypatch.setattr(pipeline, "preflight", lambda *_: {
        "status": "ready", "trade_date": "2026-09-16"})

    def run(command, state, timeout, cwd):
        step = state.stem
        if step == "collection":
            handoff = Path(command[command.index("--handoff-output") + 1])
            minute_path = tmp_path / "minutes.parquet"
            pd.DataFrame([{
                "code": "600001", "timestamp": "2026-09-16T13:30:00+08:00",
                "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
                "volume": 100.0, "amount": 1000.0,
            }]).to_parquet(minute_path, index=False)
            handoff.write_text(json.dumps({
                "generated_at": datetime.now(cn).isoformat(),
                "run_dir": str(tmp_path),
                "intraday_minutes_path": str(minute_path),
            }), encoding="utf-8")
        elif step == "scan":
            Path(command[command.index("--candidate-output") + 1]).write_text(
                '{"candidates": []}', encoding="utf-8")
        elif step == "decision":
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps(decision), encoding="utf-8")
            output.with_name("decision_结果正文.md").write_text("旧正文", encoding="utf-8")
        outcome = {"status": "stage_succeeded", "exit_code": 0}
        pipeline._save(state, outcome)
        return outcome

    monkeypatch.setattr(pipeline, "run_stage", run)


@pytest.mark.parametrize(
    ("slot", "expects_cache_only"),
    [("0945", True), ("1015", True), ("1130", True), ("1345", True),
     ("1430", True), ("1530", False)],
)
def test_intraday_slots_use_cache_only_daily_but_post_close_refreshes(slot, expects_cache_only):
    command = pipeline._collection_command(Path("handoff.json"), Path("daily_runs"), slot)
    assert ("--daily-cache-only" in command) is expects_cache_only


@pytest.mark.parametrize("slot", ["0945", "1015", "1130", "1345", "1430"])
def test_intraday_slots_defer_full_market_minutes_until_candidates_are_known(slot):
    command = pipeline._collection_command(Path("handoff.json"), Path("daily_runs"), slot)
    assert "--defer-intraday-minutes" in command


def test_find_prior_plans_keeps_the_latest_bundle_for_each_requested_slot(tmp_path):
    day = tmp_path / "scheduled" / "2026-09-16"
    for name, slot in (("0945_094500", "0945"), ("0945_094600", "0945"), ("1015_101500", "1015")):
        target = day / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "plan.json").write_text(json.dumps({
            "trade_date": "2026-09-16", "slot": slot, "plans": [],
        }), encoding="utf-8")

    plans = pipeline._find_prior_plans(
        tmp_path, "2026-09-16", before_run=day / "1130_current", source_slots=("1015", "0945")
    )

    assert plans == [day / "1015_101500" / "plan.json", day / "0945_094600" / "plan.json"]


def test_1015_collection_command_builds_its_own_static_handoff():
    command = pipeline._collection_command(Path("handoff.json"), Path("daily_runs"), "1015")

    assert command[-3:] == ["collect", "--handoff-output", "handoff.json"]
    assert "--daily-cache-only" in command
    assert "--defer-intraday-minutes" in command


def test_candidate_minute_refresh_replaces_scan_price_before_decision(tmp_path):
    candidate_path = tmp_path / "candidates.json"
    candidate_path.write_text(json.dumps({
        "trade_date": "2026-09-18",
        "observed_at": "2026-09-18T10:00:00+08:00",
        "candidates": [
            {"code": "603083", "price": 226.0},
            {"code": "603306", "price": 82.0},
        ],
    }), encoding="utf-8")
    minute_path = tmp_path / "candidate_minutes.parquet"
    pd.DataFrame([
        {"code": "603083", "timestamp": "2026-09-18T10:04:00+08:00", "close": 227.0},
        {"code": "603083", "timestamp": "2026-09-18T10:05:00+08:00", "close": 233.1},
        {"code": "603306", "timestamp": "2026-09-18T10:05:00+08:00", "close": 82.4},
    ]).to_parquet(minute_path, index=False)

    outcome = pipeline._refresh_candidate_prices(candidate_path, minute_path)

    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    rows = {row["code"]: row for row in payload["candidates"]}
    assert rows["603083"]["scan_price"] == 226.0
    assert rows["603083"]["price"] == 233.1
    assert rows["603083"]["price_source"] == "candidate_minutes_last_close"
    assert rows["603083"]["price_observed_at"] == "2026-09-18T10:05:00+08:00"
    assert rows["603306"]["price"] == 82.4
    assert payload["observed_at"] == "2026-09-18T10:05:00+08:00"
    assert outcome["updated_codes"] == ["603083", "603306"]


def test_candidate_minute_refresh_fails_closed_when_no_usable_close(tmp_path):
    candidate_path = tmp_path / "candidates.json"
    candidate_path.write_text(json.dumps({
        "trade_date": "2026-09-18",
        "observed_at": "2026-09-18T10:00:00+08:00",
        "candidates": [{"code": "603083", "price": 226.0}],
    }), encoding="utf-8")
    minute_path = tmp_path / "candidate_minutes.parquet"
    pd.DataFrame([{
        "code": "603083", "timestamp": "2026-09-18T10:05:00+08:00", "close": None,
    }]).to_parquet(minute_path, index=False)

    outcome = pipeline._refresh_candidate_prices(candidate_path, minute_path)

    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert payload["candidates"][0]["price"] == 226.0
    assert outcome["updated_codes"] == []
    assert outcome["missing_codes"] == ["603083"]


def test_0945_pipeline_is_a_full_scan_and_persists_first_plan(tmp_path, monkeypatch):
    data_root = tmp_path / "daily_runs"
    run = data_root / "scheduled" / "2026-09-16" / "0945_test"
    _stub_pipeline(monkeypatch, tmp_path, _decision())

    result = pipeline.run_pipeline(
        run, tmp_path / "report.md", data_root=data_root, slot="0945"
    )

    assert result["status"] == "report_ready"
    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    assert plan["slot"] == "0945"
    assert plan["plans"][0]["plan_id"] == "2026-09-16-0945-600000-v1"


def test_1015_pipeline_reviews_same_day_0945_plan(tmp_path, monkeypatch):
    data_root = tmp_path / "daily_runs"
    day = data_root / "scheduled" / "2026-09-16"
    prior = day / "0945_prior"
    prior.mkdir(parents=True)
    frozen = {
        "schema_version": "1.0", "trade_date": "2026-09-16", "slot": "0945",
        "first_published_at": "2026-09-16T09:47:00+08:00", "plan_status": "ready",
        "plans": [{
            "plan_id": "2026-09-16-0945-600001-v1", "version": 1,
            "slot": "0945", "code": "600001", "name": "早盘首选", "rank": 1,
            "first_published_at": "2026-09-16T09:47:00+08:00",
            "buy_zone": {"low": 9.8, "high": 10.2},
            "confirmation_price": 10.25, "no_chase_price": 10.8,
            "protection_price": 9.5, "valid_until": "2026-09-16T14:50:00+08:00",
            "opportunity_score": 80.0, "execution_score": None,
        }],
    }
    (prior / "plan.json").write_text(json.dumps(frozen), encoding="utf-8")
    current = _decision("600002", price=20.0)
    current["audit_rows"].append({
        "code": "600001", "name": "早盘首选", "price": 10.0,
        "opportunity_score": 70.0, "execution_score": 60.0, "buyable": False,
    })
    _stub_pipeline(monkeypatch, tmp_path, current)
    run = day / "1015_current"

    pipeline.run_pipeline(run, tmp_path / "report.md", data_root=data_root, slot="1015")

    decision = json.loads((run / "decision.json").read_text(encoding="utf-8"))
    assert decision["prior_plan_source"] == str(prior / "plan.json")
    assert decision["prior_plan_reviews"][0]["plan_id"] == "2026-09-16-0945-600001-v1"


def test_1130_pipeline_persists_plan_and_enriches_decision(tmp_path, monkeypatch):
    data_root = tmp_path / "daily_runs"
    run = data_root / "scheduled" / "2026-09-16" / "1130_test"
    _stub_pipeline(monkeypatch, tmp_path, _decision())

    result = pipeline.run_pipeline(
        run, tmp_path / "report.md", data_root=data_root, slot="1130"
    )

    plan = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    decision = json.loads((run / "decision.json").read_text(encoding="utf-8"))
    assert plan["plans"][0]["plan_id"] == "2026-09-16-1130-600000-v1"
    assert decision["plan_bundle"] == plan
    assert decision["plan_status"] == "ready"
    assert result["plan_path"] == str(run / "plan.json")
    assert result["plan_status"] == "ready"
    rendered = (run / "decision_结果正文.md").read_text(encoding="utf-8")
    assert "2026-09-16-1130-600000-v1" in rendered
    assert (run / "result.md").read_text(encoding="utf-8").startswith(rendered)
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == (run / "result.md").read_text(encoding="utf-8")


def test_1345_pipeline_reviews_exact_same_day_1130_plan_even_when_removed_from_top_three(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "daily_runs"
    day = data_root / "scheduled" / "2026-09-16"
    prior = day / "1130_prior"
    prior.mkdir(parents=True)
    frozen = {
        "schema_version": "1.0", "trade_date": "2026-09-16", "slot": "1130",
        "first_published_at": "2026-09-16T11:40:00+08:00", "plan_status": "ready",
        "plans": [{
            "plan_id": "2026-09-16-1130-600001-v1", "version": 1,
            "slot": "1130", "code": "600001", "name": "旧首选", "rank": 1,
            "first_published_at": "2026-09-16T11:40:00+08:00",
            "buy_zone": {"low": 9.8, "high": 10.2},
            "confirmation_price": 10.25, "no_chase_price": 10.8,
            "protection_price": 9.5, "valid_until": "2026-09-16T23:59:00+08:00",
            "opportunity_score": 80.0, "execution_score": None,
        }],
    }
    (prior / "plan.json").write_text(json.dumps(frozen), encoding="utf-8")
    current_decision = _decision("600002", price=20.0)
    current_decision["audit_rows"].append({
        "code": "600001", "name": "旧首选", "price": 10.0,
        "opportunity_score": 70.0, "execution_score": 60.0, "buyable": False,
    })
    _stub_pipeline(monkeypatch, tmp_path, current_decision)
    run = day / "1345_current"

    pipeline.run_pipeline(run, tmp_path / "report.md", data_root=data_root, slot="1345")

    decision = json.loads((run / "decision.json").read_text(encoding="utf-8"))
    review = decision["prior_plan_reviews"][0]
    assert review["code"] == "600001"
    assert review["rank_change"] == "跌出前三"
    assert review["plan_id"] == "2026-09-16-1130-600001-v1"
    assert decision["prior_plan_source"] == str(prior / "plan.json")

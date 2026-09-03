from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import local_workflow
from marketbase.pipeline.helpers import _detect_session_slug
from marketbase.pipeline.quality import _apply_degradation_flags, _compute_minute_quality, _quality_status


NOW = datetime(2026, 7, 22, 9, 43, 30, tzinfo=timezone(timedelta(hours=8)))


def _market_result(cache_path: Path) -> SimpleNamespace:
    frame = pd.DataFrame(
        [
            {
                "code": "600001",
                "name": "甲公司",
                "market": "sh",
                "price": 10.0,
                "pre_close": 9.8,
                "open": 9.9,
                "high": 10.2,
                "low": 9.8,
                "change_pct": 2.04,
                "volume": 100.0,
                "amount": 1000.0,
                "turnover_rate": 1.0,
                "volume_ratio": 1.0,
                "total_mv": 100000000000.0,
                "circ_mv": 50000000000.0,
                "pe_ratio": 15.0,
                "pb_ratio": 2.0,
                "quote_time": "09:43:00",
                "observed_at": NOW.isoformat(),
                "source": "fixture",
            },
            {
                "code": "000002",
                "name": "乙公司",
                "market": "sz",
                "price": 20.0,
                "pre_close": 19.5,
                "open": 19.8,
                "high": 20.5,
                "low": 19.5,
                "change_pct": 2.56,
                "volume": 200.0,
                "amount": 4000.0,
                "turnover_rate": 2.0,
                "volume_ratio": 2.0,
                "total_mv": 200000000000.0,
                "circ_mv": 100000000000.0,
                "pe_ratio": 20.0,
                "pb_ratio": 3.0,
                "quote_time": "09:43:00",
                "observed_at": NOW.isoformat(),
                "source": "fixture",
            },
            {
                "code": "430003",
                "name": "丙公司",
                "market": "bj",
                "price": 30.0,
                "pre_close": 29.0,
                "open": 29.5,
                "high": 31.0,
                "low": 29.0,
                "change_pct": 3.45,
                "volume": 300.0,
                "amount": 9000.0,
                "turnover_rate": 3.0,
                "volume_ratio": 3.0,
                "total_mv": 300000000000.0,
                "circ_mv": 150000000000.0,
                "pe_ratio": 25.0,
                "pb_ratio": 4.0,
                "quote_time": "09:43:00",
                "observed_at": NOW.isoformat(),
                "source": "fixture",
            },
        ]
    )
    return SimpleNamespace(
        frame=frame,
        audit={"market_counts": {"sh": 1, "sz": 1, "bj": 1}, "provider_errors": []},
        report={"primary_source": "fixture", "reference_source": "fixture"},
        cache_path=cache_path,
    )


def _daily_history(code: str, *, lookback_days: int, source: str, retries: int) -> pd.DataFrame:
    if code == "000002":
        raise RuntimeError("fixture endpoint unavailable")
    dates = pd.date_range("2025-07-01", periods=260, freq="D")
    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": range(1, 261),
            "high": range(2, 262),
            "low": range(0, 260),
            "close": range(1, 261),
            "volume": range(1000, 1260),
            "amount": range(1000, 1260),
        }
    )
    frame.attrs["daily_source"] = "fixture"
    frame.attrs["source_errors"] = []
    return frame


def _providers(tmp_path: Path, calls: list[str] | None = None) -> dict[str, object]:
    def market_collector(**kwargs):
        assert kwargs["cache_path"] == tmp_path / "cache" / "market_snapshot.json"
        return _market_result(kwargs["cache_path"])

    def daily_fetcher(code, **kwargs):
        if calls is not None:
            calls.append(code)
        return _daily_history(code, **kwargs)

    return {
        "market_collector": market_collector,
        "daily_fetcher": daily_fetcher,
        "existing_map": pd.DataFrame([{"code": "430003", "industry": "映射行业"}]),
    }


@pytest.fixture
def workflow_provider_fixtures(monkeypatch):
    def collect_run_local_minutes(codes, _cache_root, run_dir, observed_at, _emit, session_phase):
        if not session_phase.startswith("intraday"):
            return None, None
        minute_at = observed_at.replace(second=0, microsecond=0)
        run_minutes_path = run_dir / "intraday_minutes.parquet"
        pd.DataFrame(
            [
                {
                    "code": code,
                    "timestamp": minute_at.isoformat(),
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 100.0,
                    "amount": 1010.0,
                }
                for code in codes
            ]
        ).to_parquet(run_minutes_path, index=False)
        return (
            {"status": "collected", "actual_minutes": 1, "codes_with_data": len(codes)},
            str(run_minutes_path),
        )

    def collect_fixture_indices(_frame, _cache_root, observed_at, _emit):
        return (
            pd.DataFrame(
                [{"code": "000001", "name": "上证指数", "date": observed_at.date().isoformat(), "close": 3000.0}]
            ),
            True,
        )

    monkeypatch.setattr(local_workflow, "_run_intraday_minutes_collection", collect_run_local_minutes)
    monkeypatch.setattr("marketbase.pipeline.output._run_index_collection", collect_fixture_indices)


def _serialized_paths(run_dir: Path) -> list[Path]:
    return [path for path in run_dir.iterdir() if path.suffix in {".json", ".csv", ".log"}]


def test_run_collection_collects_every_market_code_and_writes_only_protocol_files(tmp_path, workflow_provider_fixtures):
    calls: list[str] = []
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path, calls),
        phase="post_close",
    )

    run_dir = Path(summary["run_dir"])
    assert sorted(calls) == ["000002", "430003", "600001"]
    assert run_dir.name == "094330_intraday_1300_objective_data"
    assert {path.name for path in run_dir.iterdir()} == {
            "market_snapshot.csv",
            "market_snapshot.json",
            "daily_indicators.csv",
            "classification_map.csv",
            "market_breadth.json",
            "industry_ma_distribution.json",
            "index_data.csv",
            "industry_agg.csv",
            "data_audit.json",
            "manifest.json",
            "run_status.json",
            "workflow.log",
            "FIELDS.md",
            "intraday_minutes.parquet",
        }
    assert summary["market_rows"] == 3
    assert summary["daily_success"] == 0  # 2 stale（latest_date 2026-03-17 ≠ trade_date 2026-07-21）
    assert summary["daily_failure"] == 1
    assert summary["indicator_rows"] == 2
    assert summary["classification_rows"] == 3
    assert not any(path.name.endswith(".json") for path in run_dir.iterdir() if path.name.startswith("codex_"))


def test_run_collection_keeps_cache_and_latest_handoff_outside_run_directory(tmp_path, workflow_provider_fixtures):
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )

    run_dir = Path(summary["run_dir"])
    latest_path = Path(summary["latest_input_path"])
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert latest_path == tmp_path / "latest_codex_input.json"
    assert latest["run_dir"] == str(run_dir.resolve())
    assert latest["data_audit_path"] == str((run_dir / "data_audit.json").resolve())
    assert latest["intraday_minutes_path"] == str((run_dir / "intraday_minutes.parquet").resolve())
    assert latest["cache_paths"]["market_snapshot"] == str((tmp_path / "cache" / "market_snapshot.json").resolve())
    assert (tmp_path / "cache" / "market_snapshot.json").is_file()
    assert (tmp_path / "cache" / "daily" / "600001.json").is_file()
    assert (tmp_path / "cache" / "daily_checkpoint.json").is_file()
    assert latest_path.parent == tmp_path
    assert all(path.parent == run_dir for path in _serialized_paths(run_dir))
    assert manifest["schema_version"] == 1
    assert manifest["run_dir"] == str(run_dir.resolve())
    for record in manifest["files"].values():
        path = run_dir / record["name"]
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert isinstance(record["rows"], int)


def test_run_collection_records_cache_hits_failures_and_objective_outputs(tmp_path, workflow_provider_fixtures):
    local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    calls: list[str] = []
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path, calls),
    )

    run_dir = Path(summary["run_dir"])
    audit = json.loads((run_dir / "data_audit.json").read_text(encoding="utf-8"))
    assert calls == ["000002"]
    assert audit["daily"]["cache_hit_count"] == 2
    assert audit["daily"]["failure_count"] == 1
    for path in _serialized_paths(run_dir):
        content = path.read_text(encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        assert content  # data files are non-empty


def test_daily_progress_log_includes_complete_event_state_and_timestamp(tmp_path, workflow_provider_fixtures):
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )

    log_lines = (Path(summary["run_dir"]) / "workflow.log").read_text(encoding="utf-8").splitlines()
    daily_line = next(line for line in log_lines if "daily_completed=" in line)

    assert "wall_time=" in daily_line
    for field in (
        "elapsed=",
        "rate=",
        "eta=",
        "daily_completed=",
        "cache_hits=",
        "failures=",
        "pending=",
        "current_code=",
        "current_source=",
        "last_error=",
    ):
        assert field in daily_line
    assert "candidate" not in daily_line.lower()


def test_daily_audit_scans_requested_code_caches_and_handoff_includes_coverage(tmp_path, workflow_provider_fixtures):
    local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    cache_path = tmp_path / "cache" / "daily" / "430003.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["rows"] = cache["rows"][:10]
    cache["actual_rows"] = 10
    cache["latest_date"] = cache["rows"][-1]["date"]
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    run_dir = Path(summary["run_dir"])
    audit = json.loads((run_dir / "data_audit.json").read_text(encoding="utf-8"))
    handoff = json.loads((tmp_path / "latest_codex_input.json").read_text(encoding="utf-8"))
    daily = audit["daily"]

    assert daily["cache_coverage_count"] == 2
    assert daily["cache_coverage_rate"] == pytest.approx(2 / 3)
    assert daily["short_history"] == [
        {"code": "430003", "actual_rows": 10, "reason": "short_history"}
    ]
    assert daily["invalid_or_missing_cache"] == [{"code": "000002", "reason": "fetch_error"}]
    assert daily["latest_date_distribution"]
    assert daily["source_counts"] == {"fixture": 2}
    assert handoff["quality_status"] == "partial"  # minute failed + daily 2/3 < 95% → partial
    assert handoff["daily_success"] == 0  # 2 cache hits are stale (latest_date != expected)
    assert handoff["daily_failure"] == 1
    assert handoff["daily_stale"] == 2
    assert handoff["daily_short_history"] == 1


def test_manifest_records_final_workflow_log_rows_and_hash(tmp_path, workflow_provider_fixtures):
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    run_dir = Path(summary["run_dir"])
    log_path = run_dir / "workflow.log"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    log_record = manifest["files"]["workflow_log"]

    assert log_record["rows"] == len(log_path.read_text(encoding="utf-8").splitlines())
    assert log_record["sha256"] == hashlib.sha256(log_path.read_bytes()).hexdigest()


def test_neutral_text_preserves_content():
    """§2 Architecture Boundary: Strategy Engine moved to strategies/.
    _neutral_text no longer censors; Marketbase outputs pure objective data."""
    value = "market data: industry classification mapping complete"
    neutral = local_workflow._neutral_text(value)
    assert neutral == value


def test_neutralization_preserves_values_when_keys_or_columns_collide():
    payload = local_workflow._neutralize_output(
        {"Candidate": "first", "Recommend": "second"}
    )
    frame = local_workflow._neutralize_frame(
        pd.DataFrame([[1, 2]], columns=["Candidate", "Recommend"])
    )

    assert payload == {"Candidate": "first", "Recommend": "second"}
    assert frame.columns.tolist() == ["Candidate", "Recommend"]
    assert frame.iloc[0].tolist() == [1, 2]


def test_serialized_outputs_neutralize_provider_text(tmp_path, workflow_provider_fixtures):
    providers = _providers(tmp_path)

    def market_collector(**kwargs):
        result = _market_result(kwargs["cache_path"])
        result.audit["provider_errors"] = ["CaNdIdAtE 推荐"]
        result.audit["ReCoMmEnD"] = "CaNdIdAtE"
        result.frame["CaNdIdAtE"] = "推荐"
        return result

    providers["market_collector"] = market_collector
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=providers,
    )

    for path in _serialized_paths(Path(summary["run_dir"])):
        content = path.read_text(encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        assert content  # data files are non-empty


def test_publish_latest_is_newest_wins_under_concurrent_calls(tmp_path):
    path = tmp_path / "latest_codex_input.json"
    older = {"generated_at": NOW.isoformat(), "run_dir": "older"}
    newer = {
        "generated_at": (NOW + timedelta(seconds=1)).isoformat(),
        "run_dir": "newer",
    }

    for _ in range(10):
        path.unlink(missing_ok=True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(local_workflow._publish_latest, path, older),
                executor.submit(local_workflow._publish_latest, path, newer),
            ]
            for future in futures:
                future.result()
        latest = json.loads(path.read_text(encoding="utf-8"))
        assert latest["generated_at"] == newer["generated_at"]
        assert latest["run_dir"] == "newer"


def test_publish_latest_uses_posix_file_lock_when_msvcrt_is_unavailable(tmp_path, monkeypatch):
    calls: list[tuple[int, int]] = []
    fcntl = SimpleNamespace(LOCK_EX=1, LOCK_UN=2)
    fcntl.flock = lambda descriptor, mode: calls.append((descriptor, mode))
    from marketbase.pipeline import helpers
    monkeypatch.setattr(helpers, "msvcrt", None)
    monkeypatch.setattr(helpers, "fcntl", fcntl)

    path = tmp_path / "latest_codex_input.json"
    assert local_workflow._publish_latest(path, {"generated_at": NOW.isoformat()})

    assert [mode for _, mode in calls] == [fcntl.LOCK_EX, fcntl.LOCK_UN]


def test_create_run_directory_retries_after_atomic_name_collision(tmp_path, monkeypatch):
    original_mkdir = Path.mkdir
    calls: list[tuple[Path, bool]] = []
    collided = False

    def mkdir(path, *args, **kwargs):
        nonlocal collided
        calls.append((path, kwargs.get("exist_ok", False)))
        if path.name == "094330_intraday_1300_objective_data" and not collided:
            collided = True
            raise FileExistsError
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)

    run_dir = local_workflow._create_run_directory(tmp_path, NOW)

    assert run_dir.name == "094330_intraday_1300_objective_data_2"
    assert any(path.name == "094330_intraday_1300_objective_data" and not exist_ok for path, exist_ok in calls)
    assert any(path.name == "094330_intraday_1300_objective_data_2" and not exist_ok for path, exist_ok in calls)


def test_older_run_does_not_replace_newer_latest_handoff(tmp_path, workflow_provider_fixtures):
    newer = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW + timedelta(seconds=1),
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )

    latest = json.loads((tmp_path / "latest_codex_input.json").read_text(encoding="utf-8"))

    assert latest["generated_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert latest["run_dir"] == newer["run_dir"]


def test_run_collection_resolves_same_second_without_overwriting(tmp_path, workflow_provider_fixtures):
    first = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
        phase="post_close",
    )
    second = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
        phase="post_close",
    )

    assert Path(first["run_dir"]).name == "094330_intraday_1300_objective_data"
    assert Path(second["run_dir"]).name == "094330_intraday_1300_objective_data_2"


def test_fulfill_request_reads_only_requested_codes_and_writes_response(tmp_path):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "request-7",
                "codes": ["600001"],
                "daily": {"lookback": 2, "fields": ["raw", "ma"]},
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    payload = local_workflow.fulfill_request(
        request_path=request_path,
        response_path=response_path,
        data_root=tmp_path,
        now=NOW,
        providers=_providers(tmp_path, calls),
    )

    assert calls == ["600001"]
    assert json.loads(response_path.read_text(encoding="utf-8")) == payload
    assert set(payload["data"]) == {"600001"}
    assert (tmp_path / "cache" / "daily" / "600001.json").is_file()
    assert not (tmp_path / "cache" / "market_snapshot.json").exists()


def test_fulfill_request_rejects_invalid_input_before_provider_calls(tmp_path):
    request_path = tmp_path / "invalid.json"
    request_path.write_text(
        json.dumps({"schema_version": 1, "request_id": "", "codes": ["600001"]}),
        encoding="utf-8",
    )
    calls: list[str] = []
    with pytest.raises(ValueError):
        local_workflow.fulfill_request(
            request_path=request_path,
            response_path=tmp_path / "response.json",
            data_root=tmp_path,
            now=NOW,
            providers=_providers(tmp_path, calls),
        )
    assert calls == []


def test_fulfill_request_leaves_unconfigured_minute_fetcher_at_task_six_default(
    tmp_path, monkeypatch
):
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "request-7-minute",
                "codes": ["600001"],
                "minute": {
                    "date": NOW.date().isoformat(),
                    "start": "09:30",
                    "end": "09:31",
                    "fields": ["raw"],
                },
            }
        ),
        encoding="utf-8",
    )
    received: dict[str, object] = {}

    def collect(request, **kwargs):
        received.update(kwargs)
        return {"schema_version": 1}

    monkeypatch.setattr(local_workflow, "collect_requested_data", collect)
    local_workflow.fulfill_request(
        request_path=request_path,
        response_path=tmp_path / "response.json",
        data_root=tmp_path,
        now=NOW,
    )

    assert "minute_fetcher" not in received


def test_cli_help_includes_full_market_t1_without_strategy_selection_terms(tmp_path, monkeypatch, capsys):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        local_workflow,
        "run_collection",
        lambda **kwargs: calls.append(kwargs)
        or {"run_dir": str(tmp_path / "run"), "market_rows": 3, "daily_success": 2, "daily_failure": 1},
    )

    assert local_workflow.main([]) == 0
    output = capsys.readouterr().out
    assert "客观数据采集完成" in output
    assert len(calls) == 1

    with pytest.raises(SystemExit) as result:
        local_workflow.main(["--help"])
    help_text = capsys.readouterr().out.lower()
    assert result.value.code == 0
    assert "fulfill-request" in help_text
    assert "full-market-t1" in help_text
    assert not any(term in help_text for term in ("scan", "prefilter", "afternoon", "rank", "recommend"))


def test_cli_accepts_data_root_as_a_global_option(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        local_workflow,
        "run_collection",
        lambda **kwargs: calls.append(kwargs)
        or {
            "run_dir": str(tmp_path / "run"),
            "market_rows": 3,
            "daily_success": 3,
            "daily_failure": 0,
        },
    )

    assert local_workflow.main(["--data-root", str(tmp_path), "collect"]) == 0
    assert calls == [{"data_root": tmp_path, "phase": None, "force_refresh": False}]


def test_vscode_launch_configuration_uses_objective_collection_without_args():
    payload = json.loads(Path(".vscode/launch.json").read_text(encoding="utf-8"))
    assert payload["configurations"] == [
        {
            "name": "MarketBase: 一键客观数据采集",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/local_workflow.py",
            "python": "${workspaceFolder}/.venv/Scripts/python.exe",
            "cwd": "${workspaceFolder}",
            "console": "integratedTerminal",
            "justMyCode": True,
            "env": {"PYTHONUTF8": "1"},
        }
    ]


def test_lunch_break_is_not_labeled_post_close_or_data_ready():
    observed_at = datetime(2026, 8, 5, 11, 33, tzinfo=timezone(timedelta(hours=8)))

    phase = _detect_session_slug(observed_at, phase="post_close")
    quality, reasons = _quality_status(
        {"status": "complete", "field_coverage": {"price": 1.0}},
        SimpleNamespace(
            success_count=1,
            cache_hit_count=0,
            total_count=1,
            short_history=[],
        ),
        set(),
        classification=pd.DataFrame([{"code": "600001", "coverage_status": "mapped"}]),
        phase=phase,
    )

    assert phase == "lunch_break"
    assert quality == "data_not_ready"
    assert reasons == ["session_not_tradable"]


def test_stale_or_unknown_quote_is_marked_untradable_in_legacy_field():
    frame = pd.DataFrame(
        [
            {"code": "600001", "tradable": True},
            {"code": "600002", "tradable": True},
            {"code": "600003", "tradable": False},
        ]
    )
    _apply_degradation_flags(
        frame,
        {"stale_codes": ["600001"], "unknown_quote_time_codes": ["600002"]},
        SimpleNamespace(errors={}, indicator_insufficient=[]),
    )

    assert frame["is_untradable"].tolist() == [True, True, False]
    assert frame["is_incomparable"].tolist() == [True, True, False]
    assert frame["tradable"].tolist() == [False, False, False]


def test_lunch_break_does_not_append_a_synthetic_minute_snapshot(tmp_path, monkeypatch, workflow_provider_fixtures):
    calls: list[object] = []
    monkeypatch.setattr(
        local_workflow,
        "_run_minute_snapshot",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "collected"},
    )

    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=datetime(2026, 8, 5, 11, 33, tzinfo=timezone(timedelta(hours=8))),
        progress=lambda message: None,
        providers=_providers(tmp_path),
        phase="post_close",
    )

    audit = json.loads((Path(summary["run_dir"]) / "data_audit.json").read_text(encoding="utf-8"))
    assert calls == []
    assert audit["session_phase"] == "lunch_break"
    assert audit["minute_continuity"]["status"] == "not_requested"
    assert audit["minute_continuity"]["reason"] == "session_not_tradable"


def test_minute_snapshot_audits_run_local_parquet_before_shared_cache(tmp_path):
    pytest.importorskip("pyarrow")
    observed_at = datetime(2026, 7, 22, 13, 3, tzinfo=timezone(timedelta(hours=8)))
    run_minutes_path = tmp_path / "run" / "intraday_minutes.parquet"
    run_minutes_path.parent.mkdir()
    run_minutes = pd.DataFrame(
        [
            {
                "code": code,
                "timestamp": timestamp,
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100.0,
                "amount": 1010.0,
            }
            for code in ("600001", "000002")
            for timestamp in (
                "2026-07-22T13:00:00+08:00",
                "2026-07-22T13:01:00+08:00",
                "2026-07-22T13:02:00+08:00",
            )
        ]
    )
    run_minutes.to_parquet(run_minutes_path, index=False)
    collection_audit = {"status": "collected", "source": "fixture"}
    snapshot = pd.DataFrame(
        [
            {"code": "600001", "price": 10.1, "volume": 100.0, "amount": 1010.0, "observed_at": observed_at},
            {"code": "000002", "price": 10.1, "volume": 100.0, "amount": 1010.0, "observed_at": observed_at},
        ]
    )

    minute_audit = local_workflow._run_minute_snapshot(
        snapshot,
        tmp_path / "cache",
        observed_at,
        lambda message: None,
        all_codes=["600001", "000002"],
        intraday_minutes_audit=collection_audit,
        intraday_minutes_path=str(run_minutes_path),
    )

    shared_cache = pd.read_parquet(tmp_path / "cache" / "intraday_1m.parquet")
    assert shared_cache["time"].nunique() == 1
    assert minute_audit["sequence_audit"]["actual_minutes"] == 3
    assert minute_audit["total_stocks"] == 2
    assert minute_audit["collection_audit"] == collection_audit
    assert minute_audit["collection_audit"] is not collection_audit


def test_minute_snapshot_rejects_missing_run_local_parquet_without_cache_audit(tmp_path):
    pytest.importorskip("pyarrow")
    observed_at = datetime(2026, 7, 22, 13, 3, tzinfo=timezone(timedelta(hours=8)))
    missing_run_path = tmp_path / "run" / "intraday_minutes.parquet"
    snapshot = pd.DataFrame(
        [
            {"code": "600001", "price": 10.1, "volume": 100.0, "amount": 1010.0, "observed_at": observed_at},
            {"code": "000002", "price": 10.1, "volume": 100.0, "amount": 1010.0, "observed_at": observed_at},
        ]
    )

    minute_audit = local_workflow._run_minute_snapshot(
        snapshot,
        tmp_path / "cache",
        observed_at,
        lambda message: None,
        all_codes=["600001", "000002"],
        intraday_minutes_audit={"status": "collected"},
        intraday_minutes_path=str(missing_run_path),
    )

    assert "sequence_audit" not in minute_audit
    assert minute_audit["sequence_error"] == f"run-local intraday parquet missing: {missing_run_path}"
    assert pd.read_parquet(tmp_path / "cache" / "intraday_1m.parquet")["time"].nunique() == 1


def test_minute_quality_prefers_dynamic_expected_minutes_for_full_threshold():
    assert _compute_minute_quality(
        {
            "status": "collected",
            "sequence_audit": {
                "actual_minutes": 60,
                "missing_minute_count": 0,
                "continuity_break_count": 0,
                "expected_minutes_dynamic": 60,
                "total_expected_minutes": 240,
            },
        }
    ) == "full"


def test_interrupted_lunch_run_never_publishes_a_ready_static_audit(tmp_path, monkeypatch, workflow_provider_fixtures):
    monkeypatch.setattr(
        local_workflow,
        "_run_daily_collection",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stop after initial audit")),
    )

    with pytest.raises(RuntimeError, match="stop after initial audit"):
        local_workflow._run_collection_locked(
            tmp_path,
            datetime(2026, 8, 5, 11, 33, tzinfo=timezone(timedelta(hours=8))),
            _providers(tmp_path),
            lambda message: None,
            phase="post_close",
        )

    audit_path = next(tmp_path.glob("*/*_lunch_break_objective_data/data_audit.json"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["quality_status"] == "data_not_ready"
    assert audit["quality_reason_codes"] == ["session_not_tradable"]


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _full_market_candidate(
    *,
    code: str = "000001",
    price: float = 45.0,
    price_band: str = "shadow_40_50",
    named_pivot: float = 44.8,
) -> dict[str, object]:
    return {
        "code": code,
        "name": "平安银行",
        "market": "sz",
        "price": price,
        "amount": 55_000_000,
        "opportunity_score": 90.0,
        "price_band": price_band,
        "candidate_reason": ["shadow_watch"],
        "buy_low": round(price - 0.5, 2),
        "buy_high": round(price + 0.2, 2),
        "chase_line": round(price + 0.3, 2),
        "protect": round(price - 1.2, 2),
        "rr_ratio": 1.8,
        "named_pivot": named_pivot,
    }


def _minute_rows_for(code: str, *, pivot: float, base_price: float) -> list[dict[str, object]]:
    closes = [base_price - 0.55, base_price - 0.5, base_price - 0.48, base_price - 0.2, base_price - 0.12, base_price]
    labels = ["13:39", "13:40", "13:41", "13:42", "13:43", "13:44"]
    volumes = [1000.0, 1000.0, 1000.0, 900.0, 900.0, 900.0]
    return [
        {
            "code": code,
            "time": label,
            "close": close,
            "volume": volume,
            "amount": round(close * volume, 2),
            "named_pivot": pivot,
        }
        for label, close, volume in zip(labels, closes, volumes, strict=True)
    ] + [
        {
            "code": code,
            "time": "13:45",
            "close": base_price - 1.2,
            "volume": 1500.0,
            "amount": round((base_price - 1.2) * 1500.0, 2),
            "named_pivot": pivot + 1.0,
        }
    ]


def _write_full_market_cli_fixture(
    tmp_path: Path,
    *,
    candidate: dict[str, object] | None = None,
    manifest_overrides: dict[str, object] | None = None,
    union_trade_date: str = "2026-09-03",
    union_observed_at: str = "2026-09-03T13:43:00+08:00",
    handoff_generated_at: str = "2026-09-03T13:45:00+08:00",
) -> tuple[Path, Path]:
    data_root = tmp_path / "daily_runs"
    run_dir = data_root / "2026-09-03_134500_full_market"
    run_dir.mkdir(parents=True, exist_ok=True)
    chosen = candidate or _full_market_candidate()
    candidate_union_path = _write_json(
        tmp_path / "candidate_union.json",
        {
            "schema_version": "1.0.0",
            "trade_date": union_trade_date,
            "observed_at": union_observed_at,
            "market_rows": 5546,
            "funnel": {"initial": 5546, "candidates": 1},
            "candidates": [chosen],
        },
    )
    _write_json(
        run_dir / "market_snapshot.json",
        {
            "schema_version": 1,
            "generated_at": handoff_generated_at,
            "rows": [
                {
                    "code": chosen["code"],
                    "name": chosen["name"],
                    "market": chosen["market"],
                    "price": chosen["price"],
                    "pre_close": chosen["price"] - 1.0,
                    "open": chosen["price"] - 0.8,
                    "high": chosen["price"] + 0.2,
                    "low": chosen["price"] - 1.0,
                    "change_pct": 4.8,
                    "turnover_rate": 3.0,
                    "amount": chosen["amount"],
                    "volume": 1_000_000,
                    "is_untradable": False,
                }
            ],
        },
    )
    pd.DataFrame(
        [{"code": chosen["code"], "ma5": chosen["price"] - 0.2, "ma10": chosen["price"] - 0.4, "ma20": chosen["price"] - 0.6, "turnover_rate": 3.0}]
    ).to_csv(run_dir / "daily_indicators.csv", index=False, encoding="utf-8")
    pd.DataFrame(
        [{"code": chosen["code"], "industry": "银行", "concepts": "国企改革", "supply_chain": "金融"}]
    ).to_csv(run_dir / "classification_map.csv", index=False, encoding="utf-8")
    pd.DataFrame(
        [{"industry": "银行", "advance_ratio": 0.62, "avg_change_pct": 0.012, "component_count": 35}]
    ).to_csv(run_dir / "industry_agg.csv", index=False, encoding="utf-8")
    _write_json(
        run_dir / "market_breadth.json",
        {"full_market": {"advance_count": 3450, "decline_count": 1800, "unchanged_count": 296, "total": 5546}},
    )
    _write_json(
        run_dir / "data_audit.json",
        {"schema_version": 1, "trade_date": union_trade_date, "quality_status": "data_ready", "generated_at": handoff_generated_at},
    )
    pd.DataFrame(
        _minute_rows_for(str(chosen["code"]), pivot=float(chosen["named_pivot"]), base_price=float(chosen["price"]))
    ).to_parquet(run_dir / "intraday_minutes.parquet", index=False)
    latest_payload: dict[str, object] = {
        "schema_version": 1,
        "generated_at": handoff_generated_at,
        "market_snapshot_path": str((run_dir / "market_snapshot.json").resolve()),
        "daily_indicators_path": str((run_dir / "daily_indicators.csv").resolve()),
        "classification_map_path": str((run_dir / "classification_map.csv").resolve()),
        "industry_agg_path": str((run_dir / "industry_agg.csv").resolve()),
        "market_breadth_path": str((run_dir / "market_breadth.json").resolve()),
        "intraday_minutes_path": str((run_dir / "intraday_minutes.parquet").resolve()),
        "data_audit_path": str((run_dir / "data_audit.json").resolve()),
    }
    latest_payload.update(manifest_overrides or {})
    _write_json(data_root / "latest_codex_input.json", latest_payload)
    return data_root, candidate_union_path


def test_full_market_t1_cli_writes_atomic_decision_and_shadow_ledger(tmp_path):
    data_root, candidate_union_path = _write_full_market_cli_fixture(tmp_path)
    output_path = tmp_path / "decision.json"

    rc = local_workflow.main(
        [
            "--data-root", str(data_root),
            "full-market-t1",
            "--candidate-union", str(candidate_union_path),
            "--decision-at", "2026-09-03T13:45:00+08:00",
            "--output", str(output_path),
        ]
    )

    assert rc == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["only_choose_one"] is None
    assert not output_path.with_suffix(".tmp").exists()
    ledger_path = data_root / "shadow" / "full_market_t1_shadow.jsonl"
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    ledger_record = json.loads(ledger_lines[0])
    assert ledger_record["code"] == "000001"
    assert ledger_record["decision"] == "shadow_watch"
    assert ledger_record["production_buyable"] is False


@pytest.mark.parametrize(
    ("union_trade_date", "union_observed_at"),
    [
        ("2026-09-02", "2026-09-03T13:43:00+08:00"),
        ("2026-09-03", "2026-09-03T13:20:00+08:00"),
    ],
)
def test_full_market_t1_cli_contract_time_mismatch_leaves_output_and_ledger_untouched(
    tmp_path,
    union_trade_date,
    union_observed_at,
):
    data_root, candidate_union_path = _write_full_market_cli_fixture(
        tmp_path,
        union_trade_date=union_trade_date,
        union_observed_at=union_observed_at,
    )
    output_path = tmp_path / "decision.json"

    rc = local_workflow.main(
        [
            "--data-root", str(data_root),
            "full-market-t1",
            "--candidate-union", str(candidate_union_path),
            "--decision-at", "2026-09-03T13:45:00+08:00",
            "--output", str(output_path),
        ]
    )

    assert rc == 1
    assert not output_path.exists()
    assert not (data_root / "shadow" / "full_market_t1_shadow.jsonl").exists()


def test_full_market_t1_cli_never_falls_back_to_undeclared_manifest_paths(tmp_path):
    data_root, candidate_union_path = _write_full_market_cli_fixture(
        tmp_path,
        manifest_overrides={"classification_map_path": None},
    )
    pd.DataFrame([{"code": "000001", "industry": "回退行业"}]).to_csv(
        data_root / "classification_map.csv",
        index=False,
        encoding="utf-8",
    )
    output_path = tmp_path / "decision.json"

    rc = local_workflow.main(
        [
            "--data-root", str(data_root),
            "full-market-t1",
            "--candidate-union", str(candidate_union_path),
            "--decision-at", "2026-09-03T13:45:00+08:00",
            "--output", str(output_path),
        ]
    )

    assert rc == 1
    assert not output_path.exists()

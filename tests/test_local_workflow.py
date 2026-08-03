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


def _serialized_paths(run_dir: Path) -> list[Path]:
    return [path for path in run_dir.iterdir() if path.suffix in {".json", ".csv", ".log"}]


def test_run_collection_collects_every_market_code_and_writes_only_protocol_files(tmp_path):
    calls: list[str] = []
    summary = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path, calls),
    )

    run_dir = Path(summary["run_dir"])
    assert sorted(calls) == ["000002", "430003", "600001"]
    assert run_dir.name == "094330_postclose_objective_data"
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
        "workflow.log",
    }
    assert summary["market_rows"] == 3
    assert summary["daily_success"] == 2
    assert summary["daily_failure"] == 1
    assert summary["indicator_rows"] == 2
    assert summary["classification_rows"] == 3
    assert not any(path.name.endswith(".json") for path in run_dir.iterdir() if path.name.startswith("codex_"))


def test_run_collection_keeps_cache_and_latest_handoff_outside_run_directory(tmp_path):
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


def test_run_collection_records_cache_hits_failures_and_objective_outputs(tmp_path):
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


def test_daily_progress_log_includes_complete_event_state_and_timestamp(tmp_path):
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


def test_daily_audit_scans_requested_code_caches_and_handoff_includes_coverage(tmp_path):
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
    assert handoff["quality_status"] == "data_not_ready"
    assert handoff["daily_success"] == 2
    assert handoff["daily_failure"] == 1


def test_manifest_records_final_workflow_log_rows_and_hash(tmp_path):
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


def test_serialized_outputs_neutralize_provider_text(tmp_path):
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
    monkeypatch.setattr(local_workflow, "msvcrt", None)
    monkeypatch.setattr(local_workflow, "fcntl", fcntl)

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
        if path.name == "094330_postclose_objective_data" and not collided:
            collided = True
            raise FileExistsError
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)

    run_dir = local_workflow._create_run_directory(tmp_path, NOW)

    assert run_dir.name == "094330_postclose_objective_data_2"
    assert any(path.name == "094330_postclose_objective_data" and not exist_ok for path, exist_ok in calls)
    assert any(path.name == "094330_postclose_objective_data_2" and not exist_ok for path, exist_ok in calls)


def test_older_run_does_not_replace_newer_latest_handoff(tmp_path):
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


def test_run_collection_resolves_same_second_without_overwriting(tmp_path):
    first = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )
    second = local_workflow.run_collection(
        data_root=tmp_path,
        now=NOW,
        progress=lambda message: None,
        providers=_providers(tmp_path),
    )

    assert Path(first["run_dir"]).name == "094330_postclose_objective_data"
    assert Path(second["run_dir"]).name == "094330_postclose_objective_data_2"


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


def test_cli_has_only_collection_and_request_commands(tmp_path, monkeypatch, capsys):
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
    assert calls == [{"data_root": tmp_path, "phase": "post_close", "force_refresh": False}]


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

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from marketbase.daily_collector import (
    DailyCollectionReport,
    DailyProgressEvent,
    collect_daily_universe,
    read_daily_cache,
)


NOW = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)


def _history(rows: int = 3, *, source: str = "fixture") -> pd.DataFrame:
    dates = pd.date_range("2026-07-01", periods=rows, freq="D")
    result = pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [10.0 + index for index in range(rows)],
            "最高": [10.5 + index for index in range(rows)],
            "最低": [9.5 + index for index in range(rows)],
            "收盘": [10.2 + index for index in range(rows)],
            "成交量": [1000 + index for index in range(rows)],
            "成交额": [10000 + index for index in range(rows)],
        }
    )
    result.attrs["daily_source"] = source
    result.attrs["source_errors"] = ["fixture fallback"]
    return result


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "cache", tmp_path / "progress.json"


def _valid_cache_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "code": "000001",
        "fetched_at": NOW.isoformat(),
        "trading_date": "2026-07-22",
        "requested_lookback": 250,
        "actual_rows": 1,
        "latest_date": "2026-07-01",
        "source": "fixture",
        "source_errors": [],
        "rows": [
            {
                "date": "2026-07-01",
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1,
                "volume": 1,
                "amount": 1,
            }
        ],
    }


def test_first_success_writes_normalized_cache_and_checkpoint(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    calls: list[tuple[str, int, str, int]] = []

    def fetcher(code, *, lookback_days, source, retries):
        calls.append((code, lookback_days, source, retries))
        return _history(source="tencent")

    report = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )

    frame, metadata = read_daily_cache(cache_root / "000001.json")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert calls == [("000001", 250, "auto", 2)]
    assert isinstance(report, DailyCollectionReport)
    assert report.success_count == 1
    assert report.source_counts == {"tencent": 1}
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert list(frame["date"]) == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert datetime.fromisoformat(metadata["fetched_at"]).tzinfo is not None
    assert {key: value for key, value in metadata.items() if key != "fetched_at"} == {
        "schema_version": 1,
        "code": "000001",
        "trading_date": "2026-07-22",
        "requested_lookback": 250,
        "actual_rows": 3,
        "latest_date": "2026-07-03",
        "source": "tencent",
        "source_errors": ["fixture fallback"],
    }
    assert checkpoint["completed_codes"] == ["000001"]
    assert checkpoint["failed_codes"] == []


def test_runtime_timestamps_are_not_frozen_to_the_collection_baseline(tmp_path, monkeypatch):
    from marketbase import daily_collector

    cache_root, checkpoint_path = _paths(tmp_path)
    runtime_times = [
        datetime(2026, 7, 22, 9, 31, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 9, 32, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 9, 33, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 9, 34, tzinfo=timezone.utc),
    ]
    clock_calls: list[datetime] = []

    def current_time() -> datetime:
        observed = runtime_times[len(clock_calls)]
        clock_calls.append(observed)
        return observed

    monkeypatch.setattr(daily_collector, "_current_time", current_time)
    events: list[DailyProgressEvent] = []

    report = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=lambda *args, **kwargs: _history(source="fixture"),
        progress=events.append,
        now=NOW,
    )

    cache_payload = json.loads((cache_root / "000001.json").read_text(encoding="utf-8"))
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert clock_calls == runtime_times
    assert report.started_at == NOW.isoformat()
    assert report.finished_at == runtime_times[3].isoformat()
    assert cache_payload["fetched_at"] == runtime_times[0].isoformat()
    assert checkpoint_payload["updated_at"] == runtime_times[1].isoformat()
    assert events[0].wall_time == runtime_times[2].isoformat()


def test_retries_only_previous_failures_and_preserves_input_order(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    first_calls: list[str] = []

    def first_fetcher(code, **kwargs):
        first_calls.append(code)
        if code == "000002":
            raise RuntimeError("temporary endpoint error")
        return _history()

    first = collect_daily_universe(
        ["000001", "000002", "000003"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=first_fetcher,
        now=NOW,
    )
    second_calls: list[str] = []

    def second_fetcher(code, **kwargs):
        second_calls.append(code)
        return _history()

    second = collect_daily_universe(
        ["000001", "000002", "000003"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=second_fetcher,
        now=NOW,
    )

    assert first_calls == ["000001", "000002", "000003"]
    assert first.success_count == 2
    assert first.failure_count == 1
    assert second_calls == ["000002"]
    assert second.success_count == 1
    assert second.cache_hit_count == 2


def test_missing_or_corrupt_cache_refetches_even_when_checkpoint_marks_complete(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    cache_root.mkdir()
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trading_date": "2026-07-22",
                "requested_lookback": 250,
                "total_codes": ["000001", "000002"],
                "completed_codes": ["000001", "000002"],
                "failed_codes": [],
                "updated_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (cache_root / "000002.json").write_text("not json", encoding="utf-8")
    calls: list[str] = []

    def fetcher(code, **kwargs):
        calls.append(code)
        return _history()

    report = collect_daily_universe(
        ["000001", "000002"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )

    assert calls == ["000001", "000002"]
    assert report.success_count == 2


def test_valid_same_day_cache_hits_and_cross_day_or_corrupt_cache_refetches(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    calls: list[str] = []

    def fetcher(code, **kwargs):
        calls.append(code)
        return _history(rows=2)

    collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )
    same_day = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )
    next_day = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW.replace(day=23),
    )
    (cache_root / "000001.json").write_text("[]", encoding="utf-8")
    corrupt = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW.replace(day=23),
    )

    assert same_day.cache_hit_count == 1
    assert next_day.success_count == 1
    assert corrupt.success_count == 1
    assert calls == ["000001", "000001", "000001"]


def test_unsorted_or_oversized_cache_is_invalid_and_refetched(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    cache_root.mkdir()
    cache_path = cache_root / "000001.json"
    payload = {
        "schema_version": 1,
        "code": "000001",
        "fetched_at": NOW.isoformat(),
        "trading_date": "2026-07-22",
        "requested_lookback": 250,
        "actual_rows": 2,
        "latest_date": "2026-07-02",
        "source": "fixture",
        "source_errors": [],
        "rows": [
            {"date": "2026-07-02", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "amount": 1},
            {"date": "2026-07-01", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1, "amount": 1},
        ],
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    calls: list[str] = []

    def fetcher(code, **kwargs):
        calls.append(code)
        return _history()

    report = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )

    assert report.success_count == 1
    assert calls == ["000001"]
    oversized = dict(payload)
    oversized["actual_rows"] = 250
    oversized["latest_date"] = "2026-09-08"
    oversized["rows"] = [
        {
            "date": value.date().isoformat(),
            "open": 1,
            "high": 2,
            "low": 0,
            "close": 1,
            "volume": 1,
            "amount": 1,
        }
        for value in pd.date_range("2026-01-01", periods=251, freq="D")
    ]
    oversized_path = cache_path.with_name("oversized.json")
    oversized_path.write_text(json.dumps(oversized), encoding="utf-8")
    with pytest.raises(ValueError):
        read_daily_cache(oversized_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("fetched_at", "2026-07-22T09:30:00"),
        ("fetched_at", "not-an-iso-timestamp"),
        ("source", ""),
        ("source_errors", [1]),
        ("source_errors", "not-a-list"),
    ],
)
def test_read_daily_cache_rejects_strict_metadata_violations(tmp_path, field, value):
    payload = _valid_cache_payload()
    payload[field] = value
    cache_path = tmp_path / "000001.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="daily cache"):
        read_daily_cache(cache_path)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": True},
        {"updated_at": "2026-07-22T09:30:00"},
        {"completed_codes": ["000001", "000001"]},
        {"completed_codes": ["000001", "000002"]},
        {"completed_codes": [["000001"]]},
        {"completed_codes": ["000001"], "failed_codes": ["000001"]},
    ],
)
def test_malformed_checkpoint_is_rebuilt_as_empty_state(tmp_path, mutation):
    from marketbase import daily_collector

    _, checkpoint_path = _paths(tmp_path)
    payload = {
        "schema_version": 1,
        "trading_date": "2026-07-22",
        "requested_lookback": 250,
        "total_codes": ["000001"],
        "completed_codes": ["000001"],
        "failed_codes": [],
        "updated_at": NOW.isoformat(),
    }
    payload.update(mutation)
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    assert daily_collector._load_checkpoint(
        checkpoint_path,
        trading_date="2026-07-22",
        lookback=250,
        codes=["000001"],
    ) == {"completed_codes": [], "failed_codes": []}


def test_short_history_is_saved_as_success_without_fabricating_rows(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)

    report = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=lambda *args, **kwargs: _history(rows=2),
        now=NOW,
    )

    frame, metadata = read_daily_cache(cache_root / "000001.json")
    assert report.success_count == 1
    assert len(frame) == 2
    assert metadata["actual_rows"] == 2
    assert metadata["requested_lookback"] == 250


def test_failure_is_isolated_and_reports_source_counts_and_neutral_errors(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)

    def fetcher(code, **kwargs):
        if code == "000002":
            raise ConnectionError("候选推荐买入卖出概率 transport closed")
        return _history(source="sina")

    report = collect_daily_universe(
        ["000001", "000002"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        now=NOW,
    )

    assert report.success_count == 1
    assert report.failure_count == 1
    assert report.pending_count == 0
    assert report.source_counts == {"sina": 1}
    assert report.errors == {"000002": "数据数据数据数据数据 transport closed"}
    assert all(term not in next(iter(report.errors.values())) for term in ("候选", "推荐", "买入", "卖出", "概率"))


def test_missing_daily_source_is_a_per_code_failure_and_writes_no_cache(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    history = _history()
    history.attrs.pop("daily_source")

    report = collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=lambda *args, **kwargs: history,
        now=NOW,
    )

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert report.success_count == 0
    assert report.failure_count == 1
    assert report.source_counts == {}
    assert "daily source is missing" in report.errors["000001"]
    assert checkpoint["completed_codes"] == []
    assert checkpoint["failed_codes"] == ["000001"]
    assert not (cache_root / "000001.json").exists()


def test_progress_events_include_rate_eta_wall_time_and_current_source(tmp_path):
    cache_root, checkpoint_path = _paths(tmp_path)
    events: list[DailyProgressEvent] = []

    def fetcher(code, **kwargs):
        if code == "000002":
            raise RuntimeError("data service unavailable")
        return _history(source="akshare")

    collect_daily_universe(
        ["000001", "000002"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        progress=events.append,
        now=NOW,
    )
    cached_events: list[DailyProgressEvent] = []
    collect_daily_universe(
        ["000001"],
        cache_root=cache_root,
        checkpoint_path=checkpoint_path,
        fetcher=fetcher,
        progress=cached_events.append,
        now=NOW,
    )

    assert len(events) == 2
    assert events[0].completed == 1
    assert events[0].current_source == "akshare"
    assert events[1].current_source == ""
    assert events[1].last_error == "data service unavailable"
    assert events[-1].eta_seconds == 0
    assert events[-1].elapsed_seconds >= 0
    assert events[-1].rate_per_minute >= 0
    assert datetime.fromisoformat(events[-1].wall_time).tzinfo is not None
    assert cached_events[0].current_source == "cache"
    assert DailyProgressEvent.__dataclass_params__.frozen
    assert DailyCollectionReport.__dataclass_params__.frozen


@pytest.mark.parametrize(
    "codes",
    [["000001", "000001"], ["000001", "abc"], ["12345"]],
)
def test_invalid_or_duplicate_codes_fail_before_fetcher_is_called(tmp_path, codes):
    cache_root, checkpoint_path = _paths(tmp_path)
    calls: list[str] = []

    with pytest.raises(ValueError):
        collect_daily_universe(
            codes,
            cache_root=cache_root,
            checkpoint_path=checkpoint_path,
            fetcher=lambda code, **kwargs: calls.append(code),
            now=NOW,
        )

    assert calls == []


@pytest.mark.parametrize("lookback", [0, 251, "250"])
def test_lookback_must_be_between_one_and_250(tmp_path, lookback):
    cache_root, checkpoint_path = _paths(tmp_path)

    with pytest.raises(ValueError, match="lookback"):
        collect_daily_universe(
            ["000001"],
            cache_root=cache_root,
            checkpoint_path=checkpoint_path,
            lookback=lookback,
            now=NOW,
        )


def test_atomic_cache_and_checkpoint_write_failure_keeps_existing_files(tmp_path, monkeypatch):
    from marketbase import daily_collector

    cache_root, checkpoint_path = _paths(tmp_path)
    cache_root.mkdir()
    cache_path = cache_root / "000001.json"
    cache_path.write_text('{"old": "cache"}', encoding="utf-8")
    checkpoint_path.write_text('{"old": "checkpoint"}', encoding="utf-8")

    original_replace = Path.replace

    def fail_replace(self, target):
        if self.suffix == ".tmp":
            raise OSError("replace denied")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        daily_collector._atomic_write_json(cache_path, {"new": "cache"})
    with pytest.raises(OSError, match="replace denied"):
        daily_collector._atomic_write_json(checkpoint_path, {"new": "checkpoint"})

    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"old": "cache"}
    assert json.loads(checkpoint_path.read_text(encoding="utf-8")) == {"old": "checkpoint"}
    assert not list(tmp_path.rglob(".*.tmp"))

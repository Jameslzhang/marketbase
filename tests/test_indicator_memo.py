from datetime import datetime, timedelta

import pandas as pd

from marketbase import daily_collector as collector


def test_indicator_cache_invalidates_on_daily_rows_and_formula(tmp_path, monkeypatch):
    frame = pd.DataFrame({"date": ["2026-09-11"], "close": [10.0]})
    now = datetime.fromisoformat("2026-09-14T11:30:00+08:00")
    calls = []
    def compute(frame, observed):
        calls.append(observed)
        return {"ma5": float(frame.close.iloc[-1]), "calculated_at": observed.isoformat()}
    monkeypatch.setattr(collector, "_compute_indicators_safe", compute)
    cache = tmp_path / "indicator.json"
    first = collector._cached_indicators(frame, now, cache)
    assert collector._cached_indicators(frame, now + timedelta(hours=2), cache) == first
    assert len(calls) == 1
    corrected = frame.copy()
    corrected.loc[0, "close"] = 11.0
    assert collector._cached_indicators(corrected, now, cache)["ma5"] == 11
    assert collector._cached_indicators(corrected, now + timedelta(days=1), cache)["ma5"] == 11
    monkeypatch.setattr(collector, "_INDICATOR_FORMULA_VERSION", "changed-formula")
    collector._cached_indicators(corrected, now + timedelta(days=1), cache)
    assert len(calls) == 3


def test_bad_cache_or_write_failure_never_blocks_calculation(tmp_path, monkeypatch):
    frame = pd.DataFrame({"date": ["2026-09-11"], "close": [10.0]})
    now = datetime.fromisoformat("2026-09-14T11:30:00+08:00")
    cache = tmp_path / "indicator.json"
    cache.write_text("broken json")
    monkeypatch.setattr(collector, "_compute_indicators_safe", lambda *_: {"ma5": 10.0})
    assert collector._cached_indicators(frame, now, cache)["ma5"] == 10
    blocker = tmp_path / "file"
    blocker.write_text("keep")
    assert collector._cached_indicators(frame, now, blocker / "indicator.json")["ma5"] == 10
    assert blocker.read_text() == "keep"


def test_failed_indicator_computation_is_not_cached(tmp_path, monkeypatch):
    frame = pd.DataFrame({"date": ["2026-09-11"], "close": [10.0]})
    now = datetime.fromisoformat("2026-09-14T11:30:00+08:00")
    cache = tmp_path / "indicator.json"
    monkeypatch.setattr(collector, "_compute_indicators_safe", lambda *_: {})
    assert collector._cached_indicators(frame, now, cache) == {}
    assert not cache.exists()


def test_collection_reuses_unchanged_indicators_without_network(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-09-14T11:30:00+08:00")
    dates = pd.bdate_range(end="2026-09-11", periods=60).strftime("%Y-%m-%d")
    frame = pd.DataFrame({"date": dates, "open": 10., "high": 11., "low": 9.,
                          "close": 10., "volume": 100., "amount": 1000.})
    frame.attrs["daily_source"] = "fixture"
    calls = []
    computations = []
    def fetch(*args, **kwargs):
        calls.append(1)
        return frame
    def compute(*args):
        computations.append(1)
        return {"ma5": 10.0}
    monkeypatch.setattr(collector, "_compute_indicators_safe", compute)
    for offset in [0, 1]:
        report = collector.collect_daily_universe(["600001"], lookback=60,
            cache_root=tmp_path / "daily", checkpoint_path=tmp_path / "progress.json",
            fetcher=fetch, now=now + timedelta(hours=offset))
        assert not report.errors, report.errors
        assert report.indicators[0]["ma5"] == 10.0
    assert len(calls) == 1
    assert len(computations) == 1
    assert (tmp_path / "daily" / "_indicator_memo.json").exists()
    assert not (tmp_path / "daily" / "_indicators").exists()

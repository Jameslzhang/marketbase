import json
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from marketbase.market_collector import OUTPUT_FIELDS, collect_market_snapshot


OBSERVED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def _live_quotes(include_bj: bool = True) -> pd.DataFrame:
    rows = [
        {
            "code": "600002",
            "name": "Shanghai",
            "price": 10.0,
            "volume": 100,
            "amount": 1000,
            "turnover_rate": 0.2,
            "volume_ratio": 1.1,
            "ticktime": "09:59:00",
        },
        {
            "code": "000002",
            "name": "Shenzhen",
            "price": 8.0,
            "volume": 200,
            "amount": 1600,
            "turnover_rate": 0.3,
            "volume_ratio": 0.9,
            "ticktime": "09:59:01",
        },
        {
            "code": "600002",
            "name": "Shanghai latest",
            "price": 10.2,
            "volume": 120,
            "amount": 1224,
            "turnover_rate": 0.21,
            "volume_ratio": 1.2,
            "ticktime": "09:59:02",
        },
    ]
    if include_bj:
        rows.append(
            {
                "code": "430001",
                "name": "Beijing",
                "price": 6.0,
                "volume": 10,
                "amount": 60,
                "turnover_rate": 0.1,
                "volume_ratio": 1.0,
                "ticktime": "09:59:03",
            }
        )
    frame = pd.DataFrame(rows)
    frame.attrs["snapshot_source"] = "sina"
    return frame


def _reference_quotes() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"code": "600002", "turnover_rate": 0.22, "volume_ratio": 1.3},
            {"code": "000002", "turnover_rate": 0.31, "volume_ratio": 1.1},
            {"code": "430001", "turnover_rate": 0.12, "volume_ratio": 1.2},
        ]
    )
    frame.attrs["snapshot_source"] = "efinance"
    return frame


def test_collect_writes_fixed_objective_snapshot_in_deterministic_order(tmp_path):
    progress: list[str] = []

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        progress=progress.append,
        primary_fetcher=_live_quotes,
        reference_fetcher=_reference_quotes,
        min_rows=1,
    )

    assert list(result.frame.columns) == list(OUTPUT_FIELDS)
    assert result.frame["code"].tolist() == ["600002", "000002", "430001"]
    assert result.frame["name"].tolist()[0] == "Shanghai latest"
    assert result.frame["market"].tolist() == ["sh", "sz", "bj"]
    for field in ("price", "volume", "amount", "turnover_rate", "volume_ratio"):
        assert pd.api.types.is_numeric_dtype(result.frame[field])
    assert result.frame["observed_at"].tolist() == [OBSERVED_AT.isoformat()] * 3
    assert result.frame["source"].tolist() == ["sina"] * 3
    assert result.audit["market_counts"] == {"sh": 1, "sz": 1, "bj": 1}
    assert result.report["primary_source"] == "sina"
    assert result.cache_path.is_file()
    payload = json.loads(result.cache_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["generated_at"] == OBSERVED_AT.isoformat()
    # Normalize NaN → None for JSON comparison (JSON serializes NaN as null)
    frame_records = result.frame.to_dict(orient="records")
    for record in frame_records:
        for key, value in record.items():
            if isinstance(value, float) and pd.isna(value):
                record[key] = None
    assert payload["rows"] == frame_records
    assert all(term not in result.frame.columns for term in ("candidate", "rank", "score"))
    assert progress and all(isinstance(item, str) and len(item) > 0 for item in progress)
    assert not any(
        term in " ".join(progress).lower()
        for term in ("candidate", "recommend", "buy", "sell", "signal", "score", "rank")
    )


def test_collect_uses_reference_fallback_and_preserves_refreshed_bse_source(
    monkeypatch, tmp_path
):
    def fallback(cached_snapshot):
        assert cached_snapshot.empty
        frame = _reference_quotes()
        frame = frame.loc[frame["code"] == "430001"].copy()
        frame["name"] = "Beijing refreshed"
        frame["price"] = 6.1
        frame["volume"] = 11
        frame["amount"] = 67.1
        frame["ticktime"] = "09:59:03"
        frame["source"] = "tencent_bse"
        frame.attrs["snapshot_source"] = "em_datacenter+tencent_bse"
        return frame

    monkeypatch.setattr(
        "marketbase.market_collector.fetch_reference_snapshot_with_bse_fallback",
        fallback,
    )

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=lambda: _live_quotes(include_bj=False),
        min_rows=1,
    )

    assert result.frame["market"].tolist() == ["sh", "sz", "bj"]
    assert result.frame.iloc[-1]["source"] == "tencent_bse"
    assert result.report["reference_source"] == "em_datacenter+tencent_bse"


def test_collect_returns_auditable_mainland_rows_when_bse_reference_fails(tmp_path):
    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=lambda: _live_quotes(include_bj=False),
        reference_fetcher=lambda: (_ for _ in ()).throw(
            RuntimeError("tencent bse buy advice endpoint unavailable")
        ),
        min_rows=1,
    )

    assert result.frame["market"].tolist() == ["sh", "sz"]
    assert "missing_market_bj" in result.audit["coverage_gaps"]
    assert result.audit["provider_errors"]
    assert "buy" not in " ".join(result.audit["provider_errors"]).lower()
    assert "buy" not in json.dumps(result.report).lower()


def test_collect_keeps_existing_cache_when_atomic_replacement_fails(monkeypatch, tmp_path):
    cache_path = tmp_path / "market.json"
    cache_path.write_text('{"previous": true}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("storage endpoint unavailable")

    monkeypatch.setattr("marketbase.market_collector.os.replace", fail_replace)

    result = collect_market_snapshot(
        cache_path=cache_path,
        now=OBSERVED_AT,
        primary_fetcher=_live_quotes,
        reference_fetcher=_reference_quotes,
        min_rows=1,
    )

    assert len(result.frame) == 3
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"previous": True}


def test_collect_audits_upstream_duplicate_and_invalid_codes_before_filtering(tmp_path):
    upstream = _live_quotes()
    invalid = pd.DataFrame([{
        "code": "not-a-code",
        "name": "Invalid",
        "price": 1.0,
        "volume": 1,
        "amount": 1.0,
        "turnover_rate": 0.1,
        "volume_ratio": 1.0,
        "ticktime": "09:59:04",
    }])
    upstream = pd.concat([invalid, upstream], ignore_index=True)

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=lambda: upstream,
        reference_fetcher=_reference_quotes,
        min_rows=1,
    )

    assert result.frame["code"].tolist() == ["600002", "000002", "430001"]
    assert result.frame["code"].is_unique
    assert result.audit["duplicate_code_count"] == 1
    assert result.audit["invalid_code_count"] == 1
    assert result.audit["market_counts"] == {"sh": 1, "sz": 1, "bj": 1}


def test_collect_audits_duplicate_and_invalid_codes_from_reference_source(tmp_path):
    reference = _reference_quotes()
    reference = pd.concat(
        [
            pd.DataFrame([{"code": "bad-reference", "volume_ratio": 1.0}]),
            reference,
            reference.loc[reference["code"].eq("430001")],
        ],
        ignore_index=True,
    )

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=_live_quotes,
        reference_fetcher=lambda: reference,
        min_rows=1,
    )

    assert result.frame["code"].tolist() == ["600002", "000002", "430001"]
    assert result.audit["duplicate_code_count"] == 2
    assert result.audit["invalid_code_count"] == 1
    assert result.audit["market_counts"] == {"sh": 1, "sz": 1, "bj": 1}


def test_collect_audits_stale_supplier_quote_time_despite_current_observation(tmp_path):
    upstream = _live_quotes()
    upstream["ticktime"] = "09:30:00"

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=lambda: upstream,
        reference_fetcher=_reference_quotes,
        min_rows=1,
    )

    assert result.frame["observed_at"].eq(OBSERVED_AT.isoformat()).all()
    assert result.audit["stale_row_count"] == 3
    assert "stale_row_count=3" in result.audit["coverage_gaps"]


def test_collect_neutralizes_cache_write_error_in_report(monkeypatch, tmp_path):
    def fail_replace(source, destination):
        raise OSError("buy \u4e70\u5165 endpoint unavailable")

    monkeypatch.setattr("marketbase.market_collector.os.replace", fail_replace)

    result = collect_market_snapshot(
        cache_path=tmp_path / "market.json",
        now=OBSERVED_AT,
        primary_fetcher=_live_quotes,
        reference_fetcher=_reference_quotes,
        min_rows=1,
    )

    assert result.report["cache_written"] is False
    assert "buy" not in result.report["cache_error"].lower()
    assert "\u4e70\u5165" not in result.report["cache_error"]


def test_collect_rejects_a_snapshot_without_usable_market_rows(tmp_path):
    invalid = pd.DataFrame([{"code": "invalid", "price": 1.0}])

    with pytest.raises(RuntimeError, match="no usable market rows"):
        collect_market_snapshot(
            cache_path=tmp_path / "market.json",
            now=OBSERVED_AT,
            primary_fetcher=lambda: invalid,
            reference_fetcher=pd.DataFrame,
            min_rows=1,
        )

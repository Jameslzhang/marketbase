from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from strategies.full_market_t1 import (
    build_candidate_union,
    classify_price_band,
    write_candidate_union,
)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (39.99, "excluded"),
        (40.00, "shadow_40_50"),
        (49.99, "shadow_40_50"),
        (50.00, "production"),
    ],
)
def test_classify_price_band_boundaries(price, expected):
    assert classify_price_band("600000", "sh", price) == expected


def test_non_main_board_is_excluded_at_any_price():
    assert classify_price_band("300001", "sz", 80.0) == "excluded"
    assert classify_price_band("301001", "sz", 80.0) == "excluded"
    assert classify_price_band("688001", "sh", 80.0) == "excluded"
    assert classify_price_band("920001", "bj", 80.0) == "excluded"


def test_build_candidate_union_preserves_metadata_and_normalizes_reasons():
    frame = pd.DataFrame(
        [
            {
                "code": "600000",
                "market": "sh",
                "name": "浦发银行",
                "price": 52.0,
                "amount": 80_000_000,
                "opportunity_tags": "trend_full|vr_good(2.0)",
            },
            {
                "code": "000001",
                "market": "sz",
                "name": "平安银行",
                "price": 45.0,
                "amount": 60_000_000,
                "opportunity_tags": ["shadow_watch"],
            },
            {
                "code": "600519",
                "market": "sh",
                "name": "贵州茅台",
                "price": 1800.0,
                "amount": 90_000_000,
            },
        ]
    )

    payload = build_candidate_union(
        frame,
        trade_date="2026-09-03",
        observed_at="2026-09-03T13:45:00+08:00",
        market_rows=5546,
        funnel={"initial": 5546, "after_hard": 812, "candidates": 37, "official": 3},
    )

    assert payload["schema_version"] == "1.0.0"
    assert payload["trade_date"] == "2026-09-03"
    assert payload["observed_at"] == "2026-09-03T13:45:00+08:00"
    assert payload["market_rows"] == 5546
    assert payload["funnel"] == {
        "initial": 5546,
        "after_hard": 812,
        "candidates": 37,
        "official": 3,
    }
    assert [row["code"] for row in payload["candidates"]] == ["600000", "000001", "600519"]
    assert payload["candidates"][0]["candidate_reason"] == ["trend_full", "vr_good(2.0)"]
    assert payload["candidates"][0]["price_band"] == "production"
    assert payload["candidates"][1]["candidate_reason"] == ["shadow_watch"]
    assert payload["candidates"][1]["price_band"] == "shadow_40_50"
    assert payload["candidates"][2]["candidate_reason"] == []
    assert payload["candidates"][2]["amount"] == 90_000_000


def test_candidate_union_is_never_buyable_at_generation_time():
    frame = pd.DataFrame(
        [
            {"code": "600000", "market": "sh", "price": 52.0, "opportunity_tags": ["trend_full"]},
            {"code": "000001", "market": "sz", "price": 45.0, "opportunity_tags": ["shadow_watch"]},
        ]
    )

    payload = build_candidate_union(
        frame,
        trade_date="2026-09-03",
        observed_at="2026-09-03T13:45:00+08:00",
        market_rows=5546,
        funnel={"initial": 5546},
    )

    assert all(row["production_buyable"] is False for row in payload["candidates"])
    assert all(row["buyable"] is False for row in payload["candidates"])
    assert all(row["only_choose_one_eligible"] is False for row in payload["candidates"])


def test_write_candidate_union_writes_utf8_json_atomically(tmp_path: Path, monkeypatch):
    target = tmp_path / "candidate_union.json"
    target.write_text('{"old": true}', encoding="utf-8")
    payload = {
        "schema_version": "1.0.0",
        "trade_date": "2026-09-03",
        "observed_at": "2026-09-03T13:45:00+08:00",
        "market_rows": 1,
        "funnel": {"initial": 1},
        "candidates": [{"code": "600000", "name": "浦发银行"}],
    }
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        src_path = Path(source)
        dst_path = Path(destination)
        replace_calls.append((src_path, dst_path))
        assert src_path.exists()
        original_replace(src_path, dst_path)

    monkeypatch.setattr("strategies.full_market_t1.os.replace", record_replace)

    result = write_candidate_union(payload, target)

    assert result == target
    assert replace_calls == [(target.with_suffix(".tmp"), target)]
    assert not target.with_suffix(".tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload

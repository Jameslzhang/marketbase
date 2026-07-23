from datetime import datetime, timedelta, timezone

import pandas as pd

from marketbase.data_audit import audit_market_snapshot


OBSERVED_AT = datetime(2026, 7, 22, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def test_audit_records_objective_quality_gaps_and_known_conditions():
    frame = pd.DataFrame(
        [
            {
                "code": "600001",
                "market": "sh",
                "price": 10.5,
                "volume": 0,
                "amount": 0,
                "turnover_rate": 0.0,
                "volume_ratio": 1.2,
                "quote_time": "09:59:00",
                "observed_at": OBSERVED_AT.isoformat(),
                "source": "sina",
            },
            {
                "code": "000002",
                "market": "sz",
                "price": 8.2,
                "volume": 100,
                "amount": 820,
                "turnover_rate": 0.3,
                "volume_ratio": None,
                "quote_time": "09:59:30",
                "observed_at": OBSERVED_AT.isoformat(),
                "source": "sina",
            },
            {
                "code": "600001",
                "market": "sh",
                "price": 10.6,
                "volume": 50,
                "amount": 530,
                "turnover_rate": 0.1,
                "volume_ratio": 1.0,
                "quote_time": "09:59:45",
                "observed_at": OBSERVED_AT.isoformat(),
                "source": "sina",
            },
            {
                "code": "bad",
                "market": "sz",
                "price": 1.0,
                "volume": 1,
                "amount": 1,
                "turnover_rate": 0.1,
                "volume_ratio": 1.0,
                "quote_time": "09:59:45",
                "observed_at": OBSERVED_AT.isoformat(),
                "source": "sina",
            },
            {
                "code": "430001",
                "market": "bj",
                "price": None,
                "volume": 2,
                "amount": 2,
                "turnover_rate": 0.1,
                "volume_ratio": 1.0,
                "quote_time": "09:30:00",
                "observed_at": (OBSERVED_AT - timedelta(minutes=30)).isoformat(),
                "source": "tencent_bse",
            },
        ]
    )

    audit = audit_market_snapshot(
        frame,
        observed_at=OBSERVED_AT,
        provider_errors=["sina: buy advice endpoint unavailable"],
    )

    assert audit["total_rows"] == 5
    assert audit["unique_code_count"] == 3
    assert audit["duplicate_code_count"] == 1
    assert audit["invalid_code_count"] == 1
    assert audit["market_counts"] == {"sh": 2, "sz": 1, "bj": 1}
    assert audit["field_non_null_counts"]["price"] == 4
    assert audit["field_coverage"]["volume_ratio"] == 0.8
    assert audit["stale_row_count"] == 1
    assert "zero_volume_with_valid_price:600001" in audit["known_conditions"]
    assert "volume_ratio_unavailable:000002" in audit["known_conditions"]
    assert "duplicate_code_count=1" in audit["coverage_gaps"]
    assert "invalid_code_count=1" in audit["coverage_gaps"]
    assert "missing_price_count=1" in audit["coverage_gaps"]
    assert "stale_row_count=1" in audit["coverage_gaps"]
    assert "buy" not in " ".join(audit["provider_errors"]).lower()


def test_audit_marks_each_expected_market_that_is_missing():
    frame = pd.DataFrame(
        [
            {
                "code": "600001",
                "market": "sh",
                "price": 10.5,
                "volume": 1,
                "amount": 10.5,
                "turnover_rate": 0.1,
                "volume_ratio": 1.0,
                "quote_time": "09:59:00",
                "observed_at": OBSERVED_AT.isoformat(),
                "source": "sina",
            }
        ]
    )

    audit = audit_market_snapshot(frame, observed_at=OBSERVED_AT)

    assert audit["coverage_gaps"] == ["missing_market_sz", "missing_market_bj"]

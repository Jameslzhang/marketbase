from datetime import datetime, timedelta, timezone

import pandas as pd

from marketbase.indicators import compute_daily_indicators, compute_vwap


def make_daily(rows: int = 250) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series(range(1, rows + 1), dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100,
            "amount": close * 100,
        }
    )


def test_daily_indicators_have_exact_keys_and_full_history_values():
    frame = make_daily()
    calculated_at = datetime(2026, 7, 22, 9, 30)

    result = compute_daily_indicators(frame, calculated_at=calculated_at)

    assert set(result) == {
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "ma120",
        "ma250",
        "rsi14",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "atr14",
        "atr14_pct",
        "input_rows",
        "first_date",
        "last_date",
        "calculated_at",
    }
    assert result["input_rows"] == 250
    assert result["first_date"] == "2025-01-01"
    assert result["last_date"] == "2025-09-07"
    assert result["calculated_at"] == "2026-07-22T09:30:00"
    assert result["ma5"] == 248.0
    assert result["ma250"] == 125.5
    assert result["rsi14"] == 100.0
    assert result["atr14"] == 2.0
    assert result["atr14_pct"] == 2.0 / 250 * 100
    assert "signal_score" not in result
    assert "macd_status" not in result
    assert "rsi_status" not in result


def test_macd_uses_ema12_ema26_dea9_and_doubled_histogram():
    close = [
        100,
        102,
        101,
        105,
        103,
        108,
        107,
        111,
        109,
        114,
        113,
        117,
        116,
        120,
        118,
        121,
        119,
        124,
        122,
        126,
        125,
        129,
        127,
        131,
        130,
        134,
        132,
        136,
        135,
        139,
        137,
        142,
        140,
        144,
        143,
    ]
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=len(close), freq="D"),
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = compute_daily_indicators(frame)

    assert result["macd_dif"] == 7.768655414725686
    assert result["macd_dea"] == 7.432468101463972
    assert result["macd_hist"] == 0.6723746265234265


def test_default_calculated_at_is_local_timezone_iso_datetime():
    before = datetime.now().astimezone()

    result = compute_daily_indicators(make_daily(1))

    calculated_at = datetime.fromisoformat(result["calculated_at"])
    after = datetime.now().astimezone()
    assert calculated_at.tzinfo is not None
    assert calculated_at.utcoffset() is not None
    assert before <= calculated_at <= after


def test_calculated_at_preserves_timezone_and_value():
    calculated_at = datetime(2026, 7, 22, 9, 30, 15, 123456, tzinfo=timezone(timedelta(hours=8)))

    result = compute_daily_indicators(make_daily(1), calculated_at=calculated_at)

    assert result["calculated_at"] == calculated_at.isoformat()


def test_short_history_returns_none_for_unavailable_indicators():
    result = compute_daily_indicators(make_daily(10))

    assert result["input_rows"] == 10
    for key in (
        "ma20",
        "ma60",
        "ma120",
        "ma250",
        "rsi14",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "atr14",
        "atr14_pct",
    ):
        assert result[key] is None
    assert result["ma5"] == 8.0


def test_vwap_handles_zero_volume_and_known_values():
    assert compute_vwap(pd.DataFrame({"price": [10, 20], "volume": [0, 0], "amount": [0, 0]})) is None
    frame = pd.DataFrame({"time": ["09:30", "09:31"], "price": [10, 20], "volume": [2, 3], "amount": [20, 60]})
    assert compute_vwap(frame) == 16.0

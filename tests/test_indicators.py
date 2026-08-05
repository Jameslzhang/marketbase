from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from marketbase.indicators import compute_daily_indicators, compute_vwap, compute_rps20


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
        "ma11",
        "ma20",
        "ma23",
        "ma60",
        "ma120",
        "ma250",
        "rsi14",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "atr14",
        "atr14_pct",
        "boll_upper",
        "boll_middle",
        "boll_lower",
        "boll_position",
        "return_5d",
        "return_10d",
        "return_20d",
        "upper_shadow_ratio",
        "lower_shadow_ratio",
        "repeated_upper_shadow",
        "overheated",
        "momentum_delta_1",
        "momentum_delta_3",
        "momentum_improving",
        "high_20d",
        "low_20d",
        "last_trade_date",
        "includes_intraday_today",
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


# ── 新增指标测试 ────────────────────────────────────────────────────


def test_bollinger_returns_expected_values():
    frame = make_daily(250)
    result = compute_daily_indicators(frame)
    # 上升趋势中，boll_middle ≈ MA20, boll_position > 0.5
    assert result["boll_middle"] == pytest.approx(241.0, rel=0.01)
    assert result["boll_upper"] > result["boll_middle"]
    assert result["boll_lower"] < result["boll_middle"]
    assert 0 < result["boll_position"] < 1


def test_bollinger_short_history_returns_none():
    result = compute_daily_indicators(make_daily(10))
    assert result["boll_upper"] is None
    assert result["boll_middle"] is None
    assert result["boll_lower"] is None
    assert result["boll_position"] is None


def test_return_n():
    close = pd.Series([100, 102, 105, 103, 108, 110])
    result = compute_daily_indicators(
        pd.DataFrame({"date": pd.date_range("2025-01-01", periods=6, freq="D"),
                      "close": close, "high": close + 1, "low": close - 1})
    )
    assert result["return_5d"] == pytest.approx(0.10, rel=0.01)  # 110/100 - 1


def test_return_n_short_history():
    result = compute_daily_indicators(make_daily(3))
    assert result["return_5d"] is None
    assert result["return_10d"] is None
    assert result["return_20d"] is None


def test_shadow_ratio():
    # 长上影线: high=110, open=100, close=101, low=99
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=250, freq="D"),
        "open": [100.0] * 250,
        "high": [110.0] * 250,
        "low": [99.0] * 250,
        "close": [101.0] * 250,
        "volume": 100,
        "amount": 10100,
    })
    result = compute_daily_indicators(frame)
    # upper_shadow = (110 - 101) / (110 - 99) = 9/11 ≈ 0.818
    assert result["upper_shadow_ratio"] == pytest.approx(9 / 11, rel=0.01)
    # lower_shadow = (100 - 99) / (110 - 99) = 1/11 ≈ 0.091
    assert result["lower_shadow_ratio"] == pytest.approx(1 / 11, rel=0.01)


def test_repeated_upper_shadow_detected():
    # 最近5天中3天有长上影
    highs = [105, 105, 105, 105, 105]
    lows = [100, 100, 100, 100, 100]
    opens = [100, 100, 100, 100, 100]
    closes = [101, 101, 104, 101, 101]  # days 1,2,4 have upper_shadow=4/5=0.8
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=5, freq="D"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": 100, "amount": 10100,
    })
    result = compute_daily_indicators(frame)
    assert result["repeated_upper_shadow"] is True


def test_repeated_upper_shadow_not_detected():
    # No repeated upper shadows
    highs = [105, 105, 105, 105, 105]
    lows = [100, 100, 100, 100, 100]
    opens = [100, 100, 100, 100, 100]
    closes = [104, 104, 104, 104, 104]  # upper_shadow=1/5=0.2 < 0.6
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=5, freq="D"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": 100, "amount": 10100,
    })
    result = compute_daily_indicators(frame)
    assert result["repeated_upper_shadow"] is False


def test_overheated_true():
    # RSI = 100 (all up), boll_position high
    frame = make_daily(250)
    result = compute_daily_indicators(frame)
    # RSI14 = 100, boll_position close to 1
    assert result["rsi14"] == 100.0
    assert result["boll_position"] > 0.8
    assert result["overheated"] is True


def test_overheated_none_when_missing():
    result = compute_daily_indicators(make_daily(10))
    assert result["rsi14"] is None
    assert result["boll_position"] is None
    assert result["overheated"] is None


def test_rps20_ranking():
    indicators = pd.DataFrame({
        "code": ["A", "B", "C", "D"],
        "return_20d": [0.10, 0.05, -0.02, 0.20],
    })
    rps = compute_rps20(indicators)
    assert rps["D"] == 100.0  # highest return
    assert rps["C"] == 25.0   # lowest return
    assert rps["A"] == 75.0
    assert rps["B"] == 50.0


def test_rps20_empty():
    assert compute_rps20(pd.DataFrame()).empty
    assert compute_rps20(pd.DataFrame({"code": ["A"], "return_20d": [None]})).empty

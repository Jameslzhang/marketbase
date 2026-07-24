"""Tests for the A-share trading calendar module."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketbase.calendar import (
    is_trading_day,
    is_cn_market_session,
    latest_trading_day,
    refresh_calendar,
)


class TestIsTradingDay:
    """Verify is_trading_day with real calendar data."""

    def test_known_trading_day(self):
        """2025-01-02 (Thursday) should be a trading day."""
        # 2025-01-01 is New Year, so 01-02 should trade
        assert is_trading_day(date(2025, 1, 2))

    def test_known_weekend_not_trading(self):
        """Saturday should not be a trading day."""
        # 2025-01-04 is a Saturday
        assert not is_trading_day(date(2025, 1, 4))

    def test_known_holiday_not_trading(self):
        """2025-01-01 (New Year) should not be a trading day."""
        assert not is_trading_day(date(2025, 1, 1))

    def test_spring_festival_not_trading(self):
        """Spring Festival 2025 (Jan 28-29) should not be trading days."""
        assert not is_trading_day(date(2025, 1, 28))
        assert not is_trading_day(date(2025, 1, 29))

    def test_national_day_not_trading(self):
        """National Day 2025 (Oct 1-3) should not be trading days."""
        assert not is_trading_day(date(2025, 10, 1))
        assert not is_trading_day(date(2025, 10, 2))
        assert not is_trading_day(date(2025, 10, 3))

    def test_accepts_datetime(self):
        """is_trading_day should accept both date and datetime."""
        assert is_trading_day(datetime(2025, 1, 2, 14, 30, tzinfo=timezone(timedelta(hours=8))))


class TestLatestTradingDay:
    """Verify latest_trading_day resolves correctly."""

    def test_normal_weekday(self):
        """On a trading day, latest_trading_day should return the same day."""
        result = latest_trading_day(date(2025, 1, 2))
        assert result == date(2025, 1, 2)

    def test_saturday_returns_friday(self):
        """On Saturday, latest_trading_day should return the preceding Friday."""
        result = latest_trading_day(date(2025, 1, 4))
        assert result == date(2025, 1, 3)  # Friday

    def test_holiday_returns_previous_trade(self):
        """On a holiday, latest_trading_day should return the previous trading day."""
        result = latest_trading_day(date(2025, 1, 1))
        assert result == date(2024, 12, 31)  # last trading day of 2024

    def test_defaults_to_today(self):
        """latest_trading_day with no argument should return today or earlier."""
        result = latest_trading_day()
        assert result <= date.today()
        assert result.weekday() < 5  # at least not a weekend


class TestIsCnMarketSession:
    """Verify is_cn_market_session trading day + time window logic."""

    CST = timezone(timedelta(hours=8))

    def test_during_session(self):
        """14:30 on a trading day should be in session."""
        dt = datetime(2025, 1, 2, 14, 30, tzinfo=self.CST)
        assert is_cn_market_session(dt)

    def test_before_session(self):
        """8:00 on a trading day should not be in session."""
        dt = datetime(2025, 1, 2, 8, 0, tzinfo=self.CST)
        assert not is_cn_market_session(dt)

    def test_after_session(self):
        """16:00 on a trading day should not be in session."""
        dt = datetime(2025, 1, 2, 16, 0, tzinfo=self.CST)
        assert not is_cn_market_session(dt)

    def test_weekend_not_in_session(self):
        """14:30 on a Saturday should not be in session."""
        dt = datetime(2025, 1, 4, 14, 30, tzinfo=self.CST)
        assert not is_cn_market_session(dt)

    def test_holiday_not_in_session(self):
        """14:30 on New Year's Day should not be in session."""
        dt = datetime(2025, 1, 1, 14, 30, tzinfo=self.CST)
        assert not is_cn_market_session(dt)


class TestRefreshCalendar:
    """Verify refresh_calendar works."""

    def test_refresh_returns_count(self):
        """Refresh should return a positive count of trading days."""
        count = refresh_calendar()
        assert count > 1000  # should have many years of trading days
"""A 股交易日历 —— 别名模块，实际实现见 marketbase.trade_calendar."""

from marketbase.trade_calendar import (
    is_trading_day,
    is_cn_market_session,
    latest_trading_day,
    refresh_calendar,
)

__all__ = [
    "is_trading_day",
    "is_cn_market_session",
    "latest_trading_day",
    "refresh_calendar",
]
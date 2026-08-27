# -*- coding: utf-8 -*-
"""实时行情窗口后端逻辑测试（marketbase/realtime_window.py）。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from marketbase import realtime_window as rw


# ── 代码解析 ─────────────────────────────────────────────────────


def test_market_of_code():
    assert rw.market_of_code("603986") == "sh"
    assert rw.market_of_code("002594") == "sz"
    assert rw.market_of_code("300750") == "sz"
    assert rw.market_of_code("430047") == "bj"
    assert rw.market_of_code("830001") == "bj"
    assert rw.market_of_code("920001") == "bj"
    assert rw.market_of_code("100001") is None


def test_normalize_code_forms():
    assert rw.normalize_code("603986") == "603986"
    assert rw.normalize_code("sh603986") == "603986"
    assert rw.normalize_code("603986.SH") == "603986"
    assert rw.normalize_code("bj430047") == "430047"
    assert rw.normalize_code("2594") == "002594"  # 自动补齐六位
    assert rw.normalize_code("abcdef") is None
    assert rw.normalize_code("1234567") is None


def test_parse_window_codes_keeps_input_order_and_dedupes():
    # 需求 7.2：按用户输入顺序、去重、补齐六位。
    assert rw.parse_window_codes("603986, bj920001 002594 603986") == [
        "603986",
        "920001",
        "002594",
    ]
    assert rw.parse_window_codes("") == []


def test_symbol_of():
    assert rw.symbol_of("603986") == "sh603986"
    assert rw.symbol_of("920001") == "bj920001"
    with pytest.raises(ValueError):
        rw.symbol_of("100001")


# ── 腾讯行情文本解析 ─────────────────────────────────────────────


def _quote_body(name: str, code: str) -> str:
    fields = [""] * 40
    fields[0] = "1"
    fields[1] = name
    fields[2] = code
    fields[3] = "395.00"  # 现价
    fields[4] = "390.00"  # 昨收
    fields[5] = "391.00"  # 开盘
    fields[30] = "20260827104705"  # 行情时间戳
    fields[31] = "5.00"  # 涨跌额
    fields[32] = "1.28"  # 涨跌幅
    fields[33] = "398.00"  # 最高
    fields[34] = "389.50"  # 最低
    fields[35] = "395.00/1234/487430000"  # 价/量(手)/额(元)
    fields[36] = "1234"  # 成交量(手)
    fields[37] = "48743"  # 成交额(万)
    fields[38] = "0.56"  # 换手率
    return "~".join(fields)


def test_parse_tencent_quote_text_fields():
    text = (
        f'v_sh603986="{_quote_body("兆易创新", "603986")}";\n'
        f'v_bj920001="{_quote_body("北交所样例", "920001")}";\n'
        'v_sz000001="too~short";\n'
    )
    quotes = rw.parse_tencent_quote_text(text, "2026-08-27 10:47:06")

    assert set(quotes) == {"603986", "920001"}  # 字段不足的行被丢弃
    sh = quotes["603986"]
    assert sh["name"] == "兆易创新"
    assert sh["price"] == pytest.approx(395.0)
    assert sh["pre_close"] == pytest.approx(390.0)
    assert sh["change_pct"] == pytest.approx(1.28)
    assert sh["high"] == pytest.approx(398.0)
    assert sh["low"] == pytest.approx(389.5)
    assert sh["volume"] == pytest.approx(123400.0)  # 手 → 股
    assert sh["amount"] == pytest.approx(487430000.0)
    assert sh["quote_time"] == "2026-08-27 10:47:05"
    assert sh["observed_at"] == "2026-08-27 10:47:06"
    assert sh["market_label"] == "沪"
    assert sh["source"] == "tencent"

    bj = quotes["920001"]
    assert bj["market"] == "bj"
    assert bj["market_label"] == "北"
    assert bj["source"] == "tencent_bse"  # 北交所独立链路标识


# ── 证券主表搜索联想 ─────────────────────────────────────────────


@pytest.fixture()
def master() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "603986", "name": "兆易创新", "market": "sh", "status": "active"},
            {"code": "000001", "name": "平安银行", "market": "sz", "status": "active"},
            {"code": "002594", "name": "比亚迪", "market": "sz", "status": "active"},
            {"code": "920001", "name": "北交所样例", "market": "bj", "status": "active"},
            {"code": "430047", "name": "诺思兰德", "market": "bj", "status": "suspended"},
        ]
    )


def test_search_by_name(master):
    results = rw.search_securities("兆易", master)
    assert results[0]["code"] == "603986"
    assert results[0]["market_label"] == "沪"
    assert results[0]["status_label"] == "正常交易"


def test_search_by_code_prefix_and_suffix(master):
    assert rw.search_securities("6039", master)[0]["code"] == "603986"
    assert rw.search_securities("3986", master)[0]["code"] == "603986"  # 后四位
    assert rw.search_securities("603986", master)[0]["code"] == "603986"  # 精确


def test_search_bj_market_label(master):
    results = rw.search_securities("920001", master)
    assert results[0]["market"] == "bj"
    assert results[0]["market_label"] == "北"


def test_search_excludes_added_and_respects_limit(master):
    assert rw.search_securities("兆易", master, exclude=["603986"]) == []
    assert len(rw.search_securities("0", master, limit=2)) == 2


def test_search_empty_inputs(master):
    assert rw.search_securities("", master) == []
    assert rw.search_securities("兆易", pd.DataFrame()) == []


def test_search_by_pinyin_initials(master):
    pytest.importorskip("pypinyin")
    assert rw.search_securities("zycx", master)[0]["code"] == "603986"
    assert rw.search_securities("z y c x", master)[0]["code"] == "603986"


def test_pinyin_initials():
    pytest.importorskip("pypinyin")
    assert rw.pinyin_initials("兆易创新") == "zycx"
    assert rw.pinyin_initials("") == ""


# ── 分时数据解析 ─────────────────────────────────────────────────


def test_parse_minute_rows_avg_price_and_volume():
    rows = [
        "0930 399.00 3302 131749800.00",
        "0931 399.50 4000 158000000.00",
    ]
    series = rw.parse_minute_rows(rows)
    assert series["times"] == ["09:30", "09:31"]
    assert series["prices"] == [399.0, 399.5]
    # 均价线 = 累计成交额 / (累计成交量 × 100)
    assert series["avg_prices"][0] == pytest.approx(399.0)
    assert series["avg_prices"][1] == pytest.approx(395.0)
    # 分时量柱 = 累计量差值（手 → 股）
    assert series["volumes"] == [330200.0, 69800.0]


def test_parse_minute_rows_skips_invalid_lines():
    series = rw.parse_minute_rows(["bad line", "0931 399.50"])
    assert series["times"] == []


# ── 交易时段状态 ─────────────────────────────────────────────────


def test_market_session_phases(monkeypatch):
    monkeypatch.setattr("marketbase.trade_calendar.is_trading_day", lambda d, **kw: True)
    assert rw.market_session(datetime(2026, 8, 27, 9, 0))["phase"] == "盘前"
    assert rw.market_session(datetime(2026, 8, 27, 10, 47))["phase"] == "盘中"
    assert rw.market_session(datetime(2026, 8, 27, 12, 0))["phase"] == "午间休市"
    assert rw.market_session(datetime(2026, 8, 27, 15, 24))["phase"] == "已收盘"
    session = rw.market_session(datetime(2026, 8, 27, 10, 47))
    assert session["clock"] == "10:47"


def test_market_session_non_trading_day(monkeypatch):
    monkeypatch.setattr("marketbase.trade_calendar.is_trading_day", lambda d, **kw: False)
    session = rw.market_session(datetime(2026, 8, 22, 10, 47))
    assert session["phase"] == "休市"
    assert session["trading_day"] is False

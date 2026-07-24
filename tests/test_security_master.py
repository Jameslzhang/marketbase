"""Tests for the security master table module."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from marketbase.security_master import (
    collect_security_master,
    get_security_universe,
    load_security_master,
    _merge_master,
    _parse_security_rows,
)


class TestParseSecurityRows:
    """Verify raw EastMoney rows are parsed correctly."""

    SAMPLE_ROWS = [
        {
            "SECUCODE": "000001.SZ",
            "SECURITY_CODE": "000001",
            "SECURITY_NAME_ABBR": "平安银行",
            "LISTING_DATE": "1991-04-03",
            "MAX_TRADE_DATE": "2026-07-24",
        },
        {
            "SECUCODE": "600519.SH",
            "SECURITY_CODE": "600519",
            "SECURITY_NAME_ABBR": "贵州茅台",
            "LISTING_DATE": "2001-08-27",
            "MAX_TRADE_DATE": "2026-07-24",
        },
        {
            "SECUCODE": "920001.BJ",
            "SECURITY_CODE": "920001",
            "SECURITY_NAME_ABBR": "测试北交",
            "LISTING_DATE": "2023-01-01",
            "MAX_TRADE_DATE": "2026-07-20",
        },
    ]

    def test_parses_sz_market(self):
        df = _parse_security_rows(self.SAMPLE_ROWS, date(2026, 7, 24))
        row = df[df["code"] == "000001"].iloc[0]
        assert row["market"] == "sz"
        assert row["name"] == "平安银行"
        assert row["listing_date"] == "1991-04-03"

    def test_parses_sh_market(self):
        df = _parse_security_rows(self.SAMPLE_ROWS, date(2026, 7, 24))
        row = df[df["code"] == "600519"].iloc[0]
        assert row["market"] == "sh"

    def test_parses_bj_market(self):
        df = _parse_security_rows(self.SAMPLE_ROWS, date(2026, 7, 24))
        row = df[df["code"] == "920001"].iloc[0]
        assert row["market"] == "bj"

    def test_active_status_when_last_trade_is_today(self):
        df = _parse_security_rows(self.SAMPLE_ROWS, date(2026, 7, 24))
        row = df[df["code"] == "000001"].iloc[0]
        assert row["status"] == "active"

    def test_suspended_status_when_last_trade_older(self):
        df = _parse_security_rows(self.SAMPLE_ROWS, date(2026, 7, 24))
        row = df[df["code"] == "920001"].iloc[0]
        assert row["status"] == "suspended"

    def test_unknown_status_when_no_last_trade(self):
        rows = [{
            "SECUCODE": "000001.SZ",
            "SECURITY_CODE": "000001",
            "SECURITY_NAME_ABBR": "测试",
            "LISTING_DATE": "",
            "MAX_TRADE_DATE": "",
        }]
        df = _parse_security_rows(rows, date(2026, 7, 24))
        assert df.iloc[0]["status"] == "unknown"

    def test_code_padded_to_6_digits(self):
        rows = [{
            "SECUCODE": "000001.SZ",
            "SECURITY_CODE": "1",
            "SECURITY_NAME_ABBR": "测试",
            "LISTING_DATE": "1991-04-03",
            "MAX_TRADE_DATE": "2026-07-24",
        }]
        df = _parse_security_rows(rows, date(2026, 7, 24))
        assert df.iloc[0]["code"] == "000001"

    def test_market_fallback_from_code_prefix(self):
        """When SECUCODE has no suffix, derive market from code prefix."""
        rows = [
            {
                "SECUCODE": "600000",
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "上海股",
                "LISTING_DATE": "1999-01-01",
                "MAX_TRADE_DATE": "2026-07-24",
            },
            {
                "SECUCODE": "000001",
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "深圳股",
                "LISTING_DATE": "1991-04-03",
                "MAX_TRADE_DATE": "2026-07-24",
            },
            {
                "SECUCODE": "300001",
                "SECURITY_CODE": "300001",
                "SECURITY_NAME_ABBR": "创业板",
                "LISTING_DATE": "2009-10-30",
                "MAX_TRADE_DATE": "2026-07-24",
            },
            {
                "SECUCODE": "920001",
                "SECURITY_CODE": "920001",
                "SECURITY_NAME_ABBR": "北交股",
                "LISTING_DATE": "2023-01-01",
                "MAX_TRADE_DATE": "2026-07-24",
            },
        ]
        df = _parse_security_rows(rows, date(2026, 7, 24))
        assert df[df["code"] == "600000"].iloc[0]["market"] == "sh"
        assert df[df["code"] == "000001"].iloc[0]["market"] == "sz"
        assert df[df["code"] == "300001"].iloc[0]["market"] == "sz"
        assert df[df["code"] == "920001"].iloc[0]["market"] == "bj"

    def test_deduplicates_by_code(self):
        rows = [
            {
                "SECUCODE": "000001.SZ",
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "first",
                "LISTING_DATE": "1991-04-03",
                "MAX_TRADE_DATE": "2026-07-24",
            },
            {
                "SECUCODE": "000001.SZ",
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "second",
                "LISTING_DATE": "1991-04-03",
                "MAX_TRADE_DATE": "2026-07-24",
            },
        ]
        df = _parse_security_rows(rows, date(2026, 7, 24))
        assert len(df) == 1
        assert df.iloc[0]["name"] == "second"  # keep last


class TestMergeMaster:
    """Verify incremental merge preserves delisted codes."""

    def test_preserves_old_delisted_codes(self):
        existing = pd.DataFrame([
            {"code": "000001", "name": "平安银行", "market": "sz",
             "listing_date": "1991-04-03", "last_trade_date": "2026-07-24",
             "status": "active", "source": "em_datacenter", "updated_at": ""},
            {"code": "999999", "name": "已退市", "market": "sz",
             "listing_date": "2000-01-01", "last_trade_date": "2020-01-01",
             "status": "suspended", "source": "em_datacenter", "updated_at": ""},
        ])
        new = pd.DataFrame([
            {"code": "000001", "name": "平安银行", "market": "sz",
             "listing_date": "1991-04-03", "last_trade_date": "2026-07-24",
             "status": "active", "source": "em_datacenter", "updated_at": ""},
        ])
        merged = _merge_master(existing, new, date(2026, 7, 24))
        assert len(merged) == 2
        assert "999999" in merged["code"].values

    def test_new_data_takes_priority(self):
        existing = pd.DataFrame([
            {"code": "000001", "name": "旧名称", "market": "sz",
             "listing_date": "1991-01-01", "last_trade_date": "2026-07-20",
             "status": "suspended", "source": "em_datacenter", "updated_at": ""},
        ])
        new = pd.DataFrame([
            {"code": "000001", "name": "新名称", "market": "sz",
             "listing_date": "1991-04-03", "last_trade_date": "2026-07-24",
             "status": "active", "source": "em_datacenter", "updated_at": ""},
        ])
        merged = _merge_master(existing, new, date(2026, 7, 24))
        assert len(merged) == 1
        assert merged.iloc[0]["name"] == "新名称"
        assert merged.iloc[0]["status"] == "active"

    def test_handles_code_normalization(self):
        existing = pd.DataFrame([
            {"code": "1", "name": "平安银行", "market": "sz",
             "listing_date": "1991-04-03", "last_trade_date": "2026-07-24",
             "status": "active", "source": "em_datacenter", "updated_at": ""},
        ])
        new = pd.DataFrame([
            {"code": "000001", "name": "平安银行", "market": "sz",
             "listing_date": "1991-04-03", "last_trade_date": "2026-07-24",
             "status": "active", "source": "em_datacenter", "updated_at": ""},
        ])
        merged = _merge_master(existing, new, date(2026, 7, 24))
        assert len(merged) == 1


class TestLoadSecurityMaster:
    """Verify load returns empty DataFrame for missing file."""

    def test_returns_empty_df_for_missing_file(self):
        df = load_security_master("/nonexistent/path/security_master.csv")
        assert df.empty
        assert "code" in df.columns
        assert "name" in df.columns

    def test_loads_existing_csv(self, tmp_path):
        p = tmp_path / "master.csv"
        df_in = pd.DataFrame({
            "code": ["000001", "600519"],
            "name": ["平安银行", "贵州茅台"],
            "market": ["sz", "sh"],
            "listing_date": ["1991-04-03", "2001-08-27"],
            "last_trade_date": ["2026-07-24", "2026-07-24"],
            "status": ["active", "active"],
            "source": ["em_datacenter", "em_datacenter"],
            "updated_at": ["", ""],
        })
        df_in.to_csv(p, index=False)
        df_out = load_security_master(p)
        assert len(df_out) == 2
        assert df_out.iloc[0]["code"] == "000001"


class TestGetSecurityUniverse:
    """Verify get_security_universe returns code lists."""

    def test_returns_empty_list_for_missing_file(self):
        codes = get_security_universe("/nonexistent/path/master.csv")
        assert codes == []

    def test_returns_all_codes_by_default(self, tmp_path):
        p = tmp_path / "master.csv"
        df_in = pd.DataFrame({
            "code": ["000001", "600519", "999999"],
            "name": ["a", "b", "c"],
            "market": ["sz", "sh", "sz"],
            "listing_date": ["", "", ""],
            "last_trade_date": ["2026-07-24", "2026-07-24", "2020-01-01"],
            "status": ["active", "active", "suspended"],
            "source": ["", "", ""],
            "updated_at": ["", "", ""],
        })
        df_in.to_csv(p, index=False)
        codes = get_security_universe(p)
        assert len(codes) == 3
        assert "999999" in codes

    def test_excludes_suspended_when_requested(self, tmp_path):
        p = tmp_path / "master.csv"
        df_in = pd.DataFrame({
            "code": ["000001", "600519", "999999"],
            "name": ["a", "b", "c"],
            "market": ["sz", "sh", "sz"],
            "listing_date": ["", "", ""],
            "last_trade_date": ["2026-07-24", "2026-07-24", "2020-01-01"],
            "status": ["active", "active", "suspended"],
            "source": ["", "", ""],
            "updated_at": ["", "", ""],
        })
        df_in.to_csv(p, index=False)
        codes = get_security_universe(p, include_suspended=False)
        assert len(codes) == 2
        assert "999999" not in codes


class TestCollectSecurityMasterIntegration:
    """Integration test with real EastMoney API."""

    def test_collects_real_data(self, tmp_path):
        """Verify collect_security_master fetches real data."""
        p = tmp_path / "master.csv"
        df = collect_security_master(p)
        assert len(df) >= 5000  # at least 5000 A-share stocks
        assert "code" in df.columns
        assert "name" in df.columns
        assert "market" in df.columns
        assert "listing_date" in df.columns
        assert "last_trade_date" in df.columns
        assert "status" in df.columns
        # Verify market distribution
        markets = df["market"].unique()
        assert "sh" in markets
        assert "sz" in markets
        assert "bj" in markets
        # Verify statuses
        assert "active" in df["status"].unique()
        # Verify file was saved
        assert p.is_file()
        loaded = pd.read_csv(p, dtype=str)
        assert len(loaded) == len(df)

    def test_collects_incremental(self, tmp_path):
        """Verify second run merges with existing data."""
        p = tmp_path / "master.csv"
        # First run
        df1 = collect_security_master(p)
        # Second run
        df2 = collect_security_master(p)
        assert len(df2) >= len(df1)  # may increase if new stocks listed
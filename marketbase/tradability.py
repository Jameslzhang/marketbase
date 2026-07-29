"""交易可执行性字段 —— 客观标注不可交易标的与板块分层.

不解释、不推荐、不排名，仅标注客观可验证的状态。
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
import re

import pandas as pd


_ST_PATTERN = re.compile(r"(?:\*?ST|退市|PT)", re.IGNORECASE)

# 涨跌停幅度（主板含中小板, 创业板, 科创板, 北交所, ST）
_LIMIT_PCT: dict[str, float] = {
    "主板": 0.10,
    "中小板": 0.10,
    "创业板": 0.20,
    "科创板": 0.20,
    "北交所": 0.30,
}
_ST_LIMIT_PCT = 0.05


def _board(code: str) -> str:
    """从代码前缀推断板块."""
    code = str(code).strip().zfill(6)
    if code.startswith(("4", "8", "9")):
        return "北交所"
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith("002"):
        return "中小板"
    return "主板"


def _is_st(name: str) -> bool:
    """从名称判断是否 ST/退市."""
    return bool(_ST_PATTERN.search(str(name)))


def _limit_pct(board: str, is_st: bool) -> float:
    if is_st:
        return _ST_LIMIT_PCT
    return _LIMIT_PCT.get(board, 0.10)


def _listing_days(security_master_df: pd.DataFrame, code: str) -> int | None:
    if security_master_df.empty or "listing_date" not in security_master_df.columns:
        return None
    row = security_master_df.loc[security_master_df["code"] == code]
    if row.empty:
        return None
    ld = row.iloc[0]["listing_date"]
    if pd.isna(ld) or str(ld).strip() == "":
        return None
    try:
        listing = datetime.strptime(str(ld)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - listing).days


def enrich_tradability(
    frame: pd.DataFrame,
    security_master_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """在快照 frame 上新增交易可执行性字段.

    新增字段:
      - is_st: 是否 ST/退市
      - is_suspended: 是否停牌（source 为 "suspended"）
      - is_limit_up: 是否涨停
      - is_limit_down: 是否跌停
      - board: 板块（主板/创业板/科创板/北交所/中小板）
      - listing_days: 上市天数
    """
    out = frame.copy()

    out["board"] = out["code"].map(_board)
    out["is_st"] = out["name"].map(_is_st) if "name" in out.columns else False
    out["is_suspended"] = (
        out["source"].astype(str).str.lower() == "suspended"
        if "source" in out.columns
        else False
    )

    sm = security_master_df if security_master_df is not None else pd.DataFrame()
    if not sm.empty and "code" in sm.columns:
        sm_indexed = sm.set_index("code")
        out["listing_days"] = out["code"].map(
            lambda c: _listing_days_from_index(sm_indexed, c)
        )
    else:
        out["listing_days"] = None

    # 涨跌停判断
    has_price = "price" in out.columns
    has_pre_close = "pre_close" in out.columns
    if has_price and has_pre_close:
        price = pd.to_numeric(out["price"], errors="coerce")
        pre_close = pd.to_numeric(out["pre_close"], errors="coerce")
        valid = price.notna() & pre_close.notna() & (pre_close > 0)
        out["is_limit_up"] = False
        out["is_limit_down"] = False
        for idx in out.index:
            if not valid.loc[idx] if hasattr(valid, "loc") else not valid.iloc[idx]:
                continue
            b = out.at[idx, "board"] if "board" in out.columns else "主板"
            st = out.at[idx, "is_st"] if "is_st" in out.columns else False
            lp = _limit_pct(str(b), bool(st))
            p = float(price.iloc[idx])
            pc = float(pre_close.iloc[idx])
            limit_up_price = round(pc * (1 + lp), 2)
            limit_down_price = round(pc * (1 - lp), 2)
            out.at[idx, "is_limit_up"] = p >= limit_up_price - 0.001
            out.at[idx, "is_limit_down"] = p <= limit_down_price + 0.001
    else:
        out["is_limit_up"] = False
        out["is_limit_down"] = False

    return out


def _listing_days_from_index(
    sm_indexed: pd.DataFrame, code: str
) -> int | None:
    if code not in sm_indexed.index:
        return None
    ld = sm_indexed.at[code, "listing_date"] if "listing_date" in sm_indexed.columns else None
    if pd.isna(ld) or str(ld).strip() == "":
        return None
    try:
        listing = datetime.strptime(str(ld)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - listing).days
"""市场级客观汇总 —— 涨跌家数、行业广度、成交分布.

纯统计计算，不输出方向性信号或排名.
"""

from __future__ import annotations

import pandas as pd


def compute_market_breadth(frame: pd.DataFrame) -> dict[str, object]:
    """计算全市场及分市场、分行业、分板块的客观广度指标.

    需要 frame 包含: code, market, price, pre_close, change_pct, amount, volume,
                   turnover_rate, industry, board (可选).

    返回:
        {
            "full_market": {...},
            "by_market": {"sh": {...}, "sz": {...}, "bj": {...}},
            "by_board": {"主板": {...}, ...},
            "by_industry": {"银行": {...}, ...}
        }
    """
    result: dict[str, object] = {}

    df = frame.copy()
    # 确保数值列
    numeric_cols = ["price", "pre_close", "change_pct", "amount", "volume", "turnover_rate"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 涨跌方向
    if "change_pct" in df.columns:
        chg = df["change_pct"]
        advance = int((chg > 0).sum())
        decline = int((chg < 0).sum())
        unchanged = int((chg == 0).sum())
    else:
        advance = decline = unchanged = 0

    def _breadth(subset: pd.DataFrame) -> dict[str, object]:
        total = len(subset)
        if total == 0:
            return {
                "total": 0, "advance_count": 0, "decline_count": 0, "unchanged_count": 0,
                "avg_change_pct": None, "median_change_pct": None,
                "total_amount": None, "avg_turnover_rate": None,
            }
        sub_chg = pd.to_numeric(subset.get("change_pct", pd.Series(dtype=float)), errors="coerce")
        sub_amt = pd.to_numeric(subset.get("amount", pd.Series(dtype=float)), errors="coerce")
        sub_to = pd.to_numeric(subset.get("turnover_rate", pd.Series(dtype=float)), errors="coerce")

        return {
            "total": total,
            "advance_count": int((sub_chg > 0).sum()),
            "decline_count": int((sub_chg < 0).sum()),
            "unchanged_count": int((sub_chg == 0).sum()),
            "avg_change_pct": round(float(sub_chg.mean()), 4) if not sub_chg.dropna().empty else None,
            "median_change_pct": round(float(sub_chg.median()), 4) if not sub_chg.dropna().empty else None,
            "total_amount": round(float(sub_amt.sum()), 2) if not sub_amt.dropna().empty else None,
            "avg_turnover_rate": round(float(sub_to.mean()), 4) if not sub_to.dropna().empty else None,
        }

    # 全市场
    result["full_market"] = _breadth(df)

    # 分市场
    by_market: dict[str, object] = {}
    if "market" in df.columns:
        for mkt in ("sh", "sz", "bj"):
            subset = df[df["market"] == mkt]
            by_market[mkt] = _breadth(subset)
    result["by_market"] = by_market

    # 分板块
    by_board: dict[str, object] = {}
    if "board" in df.columns:
        for board_name in df["board"].dropna().unique():
            subset = df[df["board"] == board_name]
            by_board[str(board_name)] = _breadth(subset)
    result["by_board"] = by_board

    # 分行业
    by_industry: dict[str, object] = {}
    if "industry" in df.columns:
        industries = df["industry"].dropna().replace("", pd.NA).dropna()
        for ind_name in industries.unique():
            subset = df[df["industry"] == ind_name]
            by_industry[str(ind_name)] = _breadth(subset)
    result["by_industry"] = by_industry

    return result
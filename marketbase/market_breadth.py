"""市场级客观汇总 —— 涨跌家数、行业广度、成交分布、行业MA分布.

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


def compute_industry_ma_distribution(
    frame: pd.DataFrame,
    indicators_df: pd.DataFrame,
) -> dict[str, object]:
    """计算每个行业在 MA5/MA10/MA20 上的成分股分布.

    用于云端判断行业同步性（行业整体是否处于趋势中）。

    frame 需要: code, industry, price
    indicators_df 需要: code, ma5, ma10, ma20

    返回:
        {
            "银行": {
                "total": 42,
                "above_ma5": 30, "above_ma5_pct": 0.714,
                "above_ma10": 28, "above_ma10_pct": 0.667,
                "above_ma20": 25, "above_ma20_pct": 0.595,
            },
            ...
        }
    """
    if frame.empty or indicators_df.empty:
        return {}

    # 检查必需列
    required = {"code", "industry", "price"}
    if not required.issubset(frame.columns):
        return {}

    # 合并行情与指标检查必需列
    required = {"code", "industry", "price"}
    if not required.issubset(frame.columns):
        return {}

    # 合并行情与指标
    merged = frame[["code", "industry", "price"]].copy()
    merged["code"] = merged["code"].astype(str).str.strip()
    cols = ["code", "ma5", "ma10", "ma20"]
    avail = [c for c in cols if c in indicators_df.columns]
    inds = indicators_df[avail].copy()
    inds["code"] = inds["code"].astype(str).str.strip()
    merged = merged.merge(inds, on="code", how="left")

    # 剔除无效行业
    merged = merged.loc[merged["industry"].notna() & (merged["industry"].astype(str).str.strip() != "")]

    result: dict[str, object] = {}
    for ind_name, group in merged.groupby("industry"):
        total = int(len(group))
        if total == 0:
            continue
        entry: dict[str, object] = {"total": total}
        for ma_col in ("ma5", "ma10", "ma20"):
            if ma_col not in group.columns:
                continue
            ma = pd.to_numeric(group[ma_col], errors="coerce")
            price = pd.to_numeric(group["price"], errors="coerce")
            valid = group.loc[ma.notna() & price.notna()]
            if valid.empty:
                entry[f"above_{ma_col}"] = 0
                entry[f"above_{ma_col}_pct"] = 0.0
                continue
            above = int((pd.to_numeric(valid["price"], errors="coerce") > pd.to_numeric(valid[ma_col], errors="coerce")).sum())
            entry[f"above_{ma_col}"] = above
            entry[f"above_{ma_col}_pct"] = round(above / total, 4)
        result[str(ind_name)] = entry

    return result
"""行业聚合 —— 从 local_workflow.py 提取.

计算行业涨跌比、等权回报、排名、成交额.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pandas as pd

_INDUSTRY_AGG_COLUMNS = [
    "industry",
    "member_count",
    "advance_ratio",
    "equal_weight_return",
    "total_amount",
    "rank",
]


def _run_industry_aggregation(
    frame: pd.DataFrame,
    _indicators_df: pd.DataFrame,
    emit: Callable[[str], None],
) -> pd.DataFrame:
    """计算行业聚合指标：涨跌比、等权回报、排名、成交额."""
    emit("industry aggregation: starting")

    if "industry" not in frame.columns:
        emit("industry aggregation: no industry column in frame")
        return pd.DataFrame(columns=_INDUSTRY_AGG_COLUMNS)

    valid = frame.dropna(subset=["industry", "price"]).copy()
    if valid.empty:
        emit("industry aggregation: no valid records")
        return pd.DataFrame(columns=_INDUSTRY_AGG_COLUMNS)

    if "change_pct" not in valid.columns:
        valid["change_pct"] = 0.0
    else:
        valid["change_pct"] = pd.to_numeric(valid["change_pct"], errors="coerce").fillna(0.0)

    grouped = valid.groupby("industry")
    agg: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
    for name, grp in grouped:
        member_count = len(grp)
        rising = (cast(pd.Series, grp["change_pct"]) > 0).sum()
        advance_ratio = rising / member_count if member_count > 0 else 0.0
        equal_weight_return = float(cast(pd.Series, grp["change_pct"]).mean()) if member_count > 0 else 0.0
        total_amount = float(cast(pd.Series, grp["amount"]).sum()) if "amount" in grp.columns else 0.0

        agg.append({
            "industry": str(name),
            "member_count": member_count,
            "advance_ratio": round(advance_ratio, 4),
            "equal_weight_return": round(equal_weight_return, 4),
            "total_amount": round(total_amount, 2),
        })

    if not agg:
        emit("industry aggregation: no industries")
        return pd.DataFrame(columns=_INDUSTRY_AGG_COLUMNS)

    df = pd.DataFrame(agg)
    df = df.sort_values(
        ["equal_weight_return", "total_amount", "industry"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)

    emit(f"industry aggregation: {len(df)} industries")
    return df.reindex(columns=_INDUSTRY_AGG_COLUMNS)
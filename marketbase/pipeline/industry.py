"""行业聚合 —— 从 local_workflow.py 提取.

计算行业客观统计指标：成分股数量、涨跌计数、涨跌比、平均涨跌幅、成交额、平均换手率.
纯客观聚合，不做任何主题强度评分或选股结论.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import pandas as pd

_INDUSTRY_AGG_COLUMNS = [
    "industry",
    "component_count",
    "advance_count",
    "decline_count",
    "advance_ratio",
    "avg_change_pct",
    "total_amount",
    "avg_turnover",
    "timestamp",
]


def _run_industry_aggregation(
    frame: pd.DataFrame,
    _indicators_df: pd.DataFrame,
    emit: Callable[[str], None],
) -> pd.DataFrame:
    """计算行业客观聚合指标：成分股数量、涨跌计数、涨跌比、平均涨跌幅、成交额、平均换手率."""
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

    if "turnover_rate" not in valid.columns:
        valid["turnover_rate"] = float("nan")
    else:
        valid["turnover_rate"] = pd.to_numeric(valid["turnover_rate"], errors="coerce")

    now_str = datetime.now(timezone.utc).isoformat()

    grouped = valid.groupby("industry")
    agg: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
    for name, grp in grouped:
        component_count = len(grp)
        change_pct_series = cast(pd.Series, grp["change_pct"])

        advance_count = int((change_pct_series > 0).sum())
        decline_count = int((change_pct_series < 0).sum())
        advance_ratio = advance_count / component_count if component_count > 0 else 0.0
        avg_change_pct = float(change_pct_series.mean()) if component_count > 0 else 0.0
        total_amount = float(cast(pd.Series, grp["amount"]).sum()) if "amount" in grp.columns else 0.0
        avg_turnover = (
            float(cast(pd.Series, grp["turnover_rate"]).mean())
            if "turnover_rate" in grp.columns and component_count > 0
            else float("nan")
        )

        agg.append({
            "industry": str(name),
            "component_count": component_count,
            "advance_count": advance_count,
            "decline_count": decline_count,
            "advance_ratio": round(advance_ratio, 4),
            "avg_change_pct": round(avg_change_pct, 4),
            "total_amount": round(total_amount, 2),
            "avg_turnover": round(avg_turnover, 4) if not pd.isna(avg_turnover) else None,
            "timestamp": now_str,
        })

    if not agg:
        emit("industry aggregation: no industries")
        return pd.DataFrame(columns=_INDUSTRY_AGG_COLUMNS)

    df = pd.DataFrame(agg)
    # Sort by industry name only — no scoring or ranking
    df = df.sort_values("industry", ignore_index=True)

    emit(f"industry aggregation: {len(df)} industries")
    return df.reindex(columns=_INDUSTRY_AGG_COLUMNS)
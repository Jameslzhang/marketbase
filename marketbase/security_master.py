"""A 股证券主表 —— 全部股票权威列表（含停牌股）。

利用东方财富选股 API 拉取全量 A 股代码、名称、上市日期和最后交易日期。
与现有本地缓存增量合并，保留已退市代码。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from marketbase.snapshot import _eastmoney_get

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────

_EM_DATACENTER_URL = "https://data.eastmoney.com/dataapi/xuangu/list"
_PAGE_SIZE = 500

_STY_FIELDS = "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,LISTING_DATE,MAX_TRADE_DATE"

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
}

_DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "daily_runs"
    / "cache"
    / "security_master.csv"
)

# ── 公开 API ─────────────────────────────────────────────────────────


def collect_security_master(
    output_path: str | Path | None = None,
    *,
    cooldown: float = 1.5,
    now: date | None = None,
) -> pd.DataFrame:
    """从东方财富拉取全量 A 股并保存为 CSV。

    分页遍历选股 API（每页 500 只，约 12 页），获取每只股票的
    上市日期和最后交易日期。与已有 CSV 合并保留已退市代码。
    """
    today = now or date.today()
    out = Path(output_path) if output_path else _DEFAULT_CACHE_PATH

    # 分页拉取全部股票
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        params = {
            "st": "SECURITY_CODE",
            "sr": "1",
            "ps": str(_PAGE_SIZE),
            "p": str(page),
            "sty": _STY_FIELDS,
            "filter": "",
            "source": "SELECT_SECURITIES",
            "client": "WEB",
        }
        resp = _eastmoney_get(
            _EM_DATACENTER_URL,
            params=params,
            headers=_HEADERS,
            timeout=30,
        )
        data = resp.json()
        if not data.get("success"):
            msg = data.get("message", "unknown error")
            raise RuntimeError(f"EastMoney xuangu failed: {msg}")

        result = data["result"]
        batch = result.get("data") or []
        if not batch:
            break

        rows.extend(batch)
        total = int(result.get("count", 0))
        fetched = len(rows)
        logger.info(
            "security master: page %d, fetched %d/%d", page, fetched, total
        )

        if fetched >= total:
            break
        page += 1

    if not rows:
        raise RuntimeError("security master: no stocks returned from EastMoney")

    df_new = _parse_security_rows(rows, today)

    # Merge with existing data
    if out.is_file():
        try:
            existing = pd.read_csv(out, dtype=str)
            df_new = _merge_master(existing, df_new, today)
        except Exception as exc:
            logger.warning("security master: could not read existing cache: %s", exc)

    # Save
    out.parent.mkdir(parents=True, exist_ok=True)
    df_new.to_csv(out, index=False, encoding="utf-8")
    logger.info("security master: saved %d stocks to %s", len(df_new), out)

    return df_new


def load_security_master(
    path: str | Path | None = None,
) -> pd.DataFrame:
    """Load the security master CSV into a DataFrame.

    Returns an empty DataFrame if the file doesn't exist.
    """
    p = Path(path) if path else _DEFAULT_CACHE_PATH
    if not p.is_file():
        return pd.DataFrame(columns=[
            "code", "name", "market", "listing_date",
            "last_trade_date", "status", "source", "updated_at",
        ])
    return pd.read_csv(p, dtype=str)


def get_security_universe(
    path: str | Path | None = None,
    *,
    include_suspended: bool = True,
) -> list[str]:
    """Return the list of all A-share stock codes.

    Parameters
    ----------
    include_suspended : bool
        If False, only return stocks with status='active'.
    """
    df = load_security_master(path)
    if df.empty:
        return []
    if not include_suspended and "status" in df.columns:
        df = df[df["status"] == "active"]
    return sorted(df["code"].dropna().unique().tolist())


# ── internal helpers ───────────────────────────────────────────────────


def _parse_security_rows(
    rows: list[dict[str, object]], today: date
) -> pd.DataFrame:
    """Convert raw EastMoney rows into a standardized DataFrame."""
    records: list[dict[str, object]] = []
    now_iso = datetime.now().isoformat()

    for row in rows:
        secucode = str(row.get("SECUCODE", ""))
        code = str(row.get("SECURITY_CODE", "")).zfill(6)
        name = str(row.get("SECURITY_NAME_ABBR", ""))
        listing_date = str(row.get("LISTING_DATE", ""))
        last_trade = str(row.get("MAX_TRADE_DATE", ""))

        # Derive market from SECUCODE suffix (.SZ, .SH, .BJ)
        market = ""
        if secucode.endswith(".SZ"):
            market = "sz"
        elif secucode.endswith(".SH"):
            market = "sh"
        elif secucode.endswith(".BJ"):
            market = "bj"
        else:
            # Fallback: derive from code prefix
            if code.startswith("6"):
                market = "sh"
            elif code.startswith(("0", "3")):
                market = "sz"
            elif code.startswith(("4", "8", "9")):
                market = "bj"

        # Status inference
        if last_trade:
            try:
                last_date = date.fromisoformat(last_trade)
                status = "active" if last_date >= today else "suspended"
            except ValueError:
                status = "unknown"
        else:
            status = "unknown"

        records.append({
            "code": code,
            "name": name,
            "market": market,
            "listing_date": listing_date if listing_date else "",
            "last_trade_date": last_trade,
            "status": status,
            "source": "em_datacenter",
            "updated_at": now_iso,
        })

    df = pd.DataFrame(records)
    df = df.drop_duplicates("code", keep="last").reset_index(drop=True)
    return df


def _merge_master(
    existing: pd.DataFrame,
    new: pd.DataFrame,
    today: date,
) -> pd.DataFrame:
    """Merge new data with existing cache, preserving old delisted codes."""
    if "code" not in existing.columns:
        return new

    # Normalize codes
    existing["code"] = existing["code"].astype(str).str.zfill(6)
    new["code"] = new["code"].astype(str).str.zfill(6)

    # Codes that exist in old but not in new (possibly delisted)
    old_only = existing[~existing["code"].isin(new["code"])].copy()

    # Merge: new data takes priority, but mark overlapping codes
    merged = new.copy()

    if not old_only.empty:
        # Keep old codes that have disappeared
        merged = pd.concat([merged, old_only], ignore_index=True, sort=False)

    merged = merged.drop_duplicates("code", keep="first").reset_index(drop=True)

    logger.info(
        "security master merge: %d new, %d old-only, %d merged",
        len(new), len(old_only), len(merged),
    )
    return merged
"""Industry & concept classification collection from EastMoney datacenter.

Collects industry, concept, and board classification for all A-share stocks
using the EastMoney xuangu API.  Written to classification_source.csv as the
authoritative human-curated reference (programmatically maintained).

Strategy: The xuangu API has a hard per-query response limit (~500 items).
We work around this by querying **per-industry** using the INDUSTRY filter,
which returns different subsets for each industry name.  Each run merges new
results with existing data (incremental accumulation), so coverage grows over
time.  A full coverage run requires ~90 industry queries (one per industry).

This is a standalone module — not part of the main snapshot pipeline — because
classification data changes slowly and needs infrequent updates.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

from marketbase.snapshot import _eastmoney_get

_EM_DATACENTER_URL = "https://data.eastmoney.com/dataapi/xuangu/list"
_PUSH2_URL = "https://push2.eastmoney.com/api/qt/clist/get"

_CLASSIFICATION_FIELDS = [
    "code", "name", "industry", "concepts",
    "supply_chain", "source", "updated_at",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/xuangu/",
}

# ——— industry list ————————————————————————————————————————————————

def _get_industry_names() -> list[str]:
    """Return the list of A-share industry names (东方行业分类).

    Tries push2 board API first (full list), falls back to extracting
    industry names from a broad xuangu query.
    """
    # Try push2 board API for full industry list
    try:
        resp = _eastmoney_get(_PUSH2_URL, params={
            "pn": "1", "pz": "200", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:90+t:2", "fields": "f12,f14",
        }, headers=_HEADERS, timeout=30)
        data = resp.json()
        if data.get("data") and data["data"].get("diff"):
            names = [it["f14"] for it in data["data"]["diff"] if it.get("f14")]
            if names:
                logger.info("got %d industry names from push2 board API", len(names))
                return names
    except Exception as exc:
        logger.debug("push2 industry list failed: %s", exc)

    # Fallback: extract from a broad xuangu query
    logger.info("falling back to xuangu for industry list")
    resp = _eastmoney_get(_EM_DATACENTER_URL, params={
        "st": "SECURITY_CODE", "sr": "1", "ps": "500", "p": "1",
        "sty": "INDUSTRY",
        "filter": '(MARKET+in+("上交所主板","深交所主板","深交所创业板","上交所科创板","北交所"))',
        "source": "SELECT_SECURITIES", "client": "WEB",
    }, headers=_HEADERS, timeout=30)
    data = resp.json()
    seen: set[str] = set()
    for item in data["result"]["data"]:
        ind = str(item.get("INDUSTRY", "")).strip()
        if ind:
            seen.add(ind)
    names = sorted(seen)
    logger.info("got %d industry names from xuangu fallback", len(names))
    return names


# ——— per-industry fetch —————————————————————————————————————————————

def _fetch_industry(industry: str) -> list[dict[str, str]]:
    """Fetch all stocks for a single industry via xuangu INDUSTRY filter."""
    resp = _eastmoney_get(_EM_DATACENTER_URL, params={
        "st": "SECURITY_CODE", "sr": "1", "ps": "500", "p": "1",
        "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,INDUSTRY,CONCEPT,BOARD_NAME",
        "filter": f'(INDUSTRY+in+("{industry}"))',
        "source": "SELECT_SECURITIES", "client": "WEB",
    }, headers=_HEADERS, timeout=30)
    data = resp.json()
    items = data["result"]["data"]

    rows: list[dict[str, str]] = []
    for item in items:
        code = str(item.get("SECURITY_CODE", "")).strip()
        if not code or len(code) != 6:
            continue
        name = str(item.get("SECURITY_NAME_ABBR", "")).strip()
        ind = str(item.get("INDUSTRY", "")).strip()
        board = str(item.get("BOARD_NAME", "")).strip()

        concepts_raw = item.get("CONCEPT", [])
        if isinstance(concepts_raw, list):
            concepts = ", ".join(str(c).strip() for c in concepts_raw if str(c).strip())
        else:
            concepts = str(concepts_raw).strip() if concepts_raw else ""

        rows.append({
            "code": code,
            "name": name,
            "industry": ind or board,
            "concepts": concepts,
        })
    return rows


# ——— public API ————————————————————————————————————————————————————

def collect_classification(
    output_path: str | Path,
    *,
    cooldown: float = 1.5,
) -> pd.DataFrame:
    """Collect A-share industry/concept classification and save to CSV.

    Queries each industry individually via the xuangu API to bypass the
    ~500-item per-query limit.  Merges with any existing data at
    *output_path* (incremental mode).

    Returns the merged classification DataFrame.
    """
    output_path = Path(output_path)
    now_str = datetime.now(timezone.utc).isoformat()

    # Load existing data if any
    existing: dict[str, dict[str, str]] = {}
    if output_path.is_file():
        try:
            df_existing = pd.read_csv(output_path, dtype=str, keep_default_na=False)
            df_existing = df_existing[~df_existing["code"].str.startswith("#", na=False)]
            for _, row in df_existing.iterrows():
                existing[row["code"]] = row.to_dict()
            logger.info("loaded %d existing rows from %s", len(existing), output_path)
        except Exception as exc:
            logger.warning("could not load existing %s: %s", output_path, exc)

    # Get industry list
    industries = _get_industry_names()
    if not industries:
        raise RuntimeError("no industry names available")

    # Query each industry
    new_count = 0
    for i, industry in enumerate(industries):
        if i > 0:
            time.sleep(cooldown)

        try:
            rows = _fetch_industry(industry)
        except Exception as exc:
            logger.warning("[%d/%d] %s: ERROR %s", i + 1, len(industries), industry, exc)
            continue

        added = 0
        for row in rows:
            code = row["code"]
            if code not in existing:
                existing[code] = {**row, "supply_chain": "", "source": "em_datacenter", "updated_at": now_str}
                added += 1
        new_count += added
        logger.info(
            "[%d/%d] %s: %d items, %d new (total: %d)",
            i + 1, len(industries), industry, len(rows), added, len(existing),
        )

    if not existing:
        raise RuntimeError("em_datacenter classification returned no data")

    # Build and save DataFrame
    df = pd.DataFrame(list(existing.values()), columns=_CLASSIFICATION_FIELDS)
    df = df.sort_values("code", ignore_index=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")

    industry_filled = (df["industry"] != "").sum()
    concepts_filled = (df["concepts"] != "").sum()
    logger.info(
        "classification saved: %d rows (%d new), industry=%d, concepts=%d",
        len(df), new_count, industry_filled, concepts_filled,
    )
    return df


def load_classification(path: str | Path) -> pd.DataFrame:
    """Load classification_source.csv, skipping comment lines."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Classification file not found: {path}")
    return pd.read_csv(path, dtype=str, encoding="utf-8", keep_default_na=False, comment="#")
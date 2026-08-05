"""Industry & concept classification collection from EastMoney datacenter.

Collects industry, concept, and board classification for all A-share stocks
using the EastMoney xuangu API.  Written to classification_source.csv as the
authoritative human-curated reference (programmatically maintained).

Strategy: The xuangu API has a hard per-query response limit (~500 items).
We work around this by querying **per-industry** using the INDUSTRY filter,
which returns different subsets for each industry name.  Each run merges new
results with existing data (incremental accumulation), so coverage grows over
time.  A full coverage run requires ~90 industry queries (one per industry).

For stocks not covered by the per-industry approach, a fallback queries each
uncovered stock by its code directly via the xuangu API, and then a Sina
fallback attempts to cover any remaining uncovered stocks.

This is a standalone module — not part of the main snapshot pipeline — because
classification data changes slowly and needs infrequent updates.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

from marketbase.snapshot import _eastmoney_get

_EM_DATACENTER_URL = "https://data.eastmoney.com/dataapi/xuangu/list"
_PUSH2_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_SINA_CORP_INFO_URL = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/{code}.phtml"

_CLASSIFICATION_FIELDS = [
    "code", "name", "industry", "concepts",
    "supply_chain", "source", "updated_at", "coverage_status",
]
_SUPPLY_CHAIN_FIELDS = [
    "theme", "code", "role", "relation_type", "evidence_source", "updated_at",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/xuangu/",
}
_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
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


# ——— fallback: query by stock code (EastMoney) ——————————————————————

def _fetch_by_code(code: str) -> dict[str, str] | None:
    """Fetch classification for a single stock by its code via EastMoney."""
    resp = _eastmoney_get(_EM_DATACENTER_URL, params={
        "st": "SECURITY_CODE", "sr": "1", "ps": "10", "p": "1",
        "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,INDUSTRY,CONCEPT,BOARD_NAME",
        "filter": f'(SECURITY_CODE+in+("{code}"))',
        "source": "SELECT_SECURITIES", "client": "WEB",
    }, headers=_HEADERS, timeout=30)
    data = resp.json()
    items = data.get("result", {}).get("data", [])
    if not items:
        return None

    item = items[0]
    code_val = str(item.get("SECURITY_CODE", "")).strip()
    if not code_val or len(code_val) != 6:
        return None

    name = str(item.get("SECURITY_NAME_ABBR", "")).strip()
    ind = str(item.get("INDUSTRY", "")).strip()
    board = str(item.get("BOARD_NAME", "")).strip()

    concepts_raw = item.get("CONCEPT", [])
    if isinstance(concepts_raw, list):
        concepts = ", ".join(str(c).strip() for c in concepts_raw if str(c).strip())
    else:
        concepts = str(concepts_raw).strip() if concepts_raw else ""

    return {
        "code": code_val,
        "name": name,
        "industry": ind or board,
        "concepts": concepts,
    }


# ——— fallback: Sina finance —————————————————————————————————————————

_SINA_INDUSTRY_RE = re.compile(
    r'所属行业[：:]\s*</td>\s*<td[^>]*>\s*(?:<a[^>]*>)?([^<]+)'
)


def _fetch_sina_by_code(code: str) -> dict[str, str] | None:
    """Fetch classification for a single stock from Sina finance.

    Parses the Sina corporate info page to extract the industry field.
    Returns None if the page cannot be fetched or parsed.
    """
    url = _SINA_CORP_INFO_URL.format(code=code)
    try:
        resp = requests.get(url, headers=_SINA_HEADERS, timeout=15)
        resp.encoding = "gb2312"
    except Exception as exc:
        logger.debug("sina fallback [%s]: HTTP error %s", code, exc)
        return None

    if resp.status_code != 200:
        logger.debug("sina fallback [%s]: status %d", code, resp.status_code)
        return None

    text = resp.text
    match = _SINA_INDUSTRY_RE.search(text)
    if not match:
        logger.debug("sina fallback [%s]: no industry found in page", code)
        return None

    industry = match.group(1).strip()
    if not industry:
        return None

    return {
        "code": code,
        "name": "",
        "industry": industry,
        "concepts": "",
    }


def _collect_sina_fallback(
    existing: dict[str, dict[str, str]],
    uncovered_codes: list[str],
    now_str: str,
    cooldown: float,
) -> int:
    """Fallback: query each uncovered stock via Sina finance.

    Returns the number of newly covered stocks.
    """
    if not uncovered_codes:
        return 0

    logger.info(
        "sina fallback: querying %d uncovered codes via Sina finance",
        len(uncovered_codes),
    )
    sina_new = 0
    for i, code in enumerate(uncovered_codes):
        if i > 0:
            time.sleep(cooldown)

        try:
            row = _fetch_sina_by_code(code)
        except Exception as exc:
            logger.debug(
                "sina fallback [%d/%d] %s: ERROR %s",
                i + 1, len(uncovered_codes), code, exc,
            )
            continue

        if row is None:
            continue

        if code not in existing:
            existing[code] = {
                **row,
                "supply_chain": "",
                "source": "sina_finance",
                "updated_at": now_str,
            }
            sina_new += 1

        if (i + 1) % 50 == 0:
            logger.info(
                "sina fallback [%d/%d]: %d new (total: %d)",
                i + 1, len(uncovered_codes), sina_new, len(existing),
            )

    logger.info(
        "sina fallback complete: %d uncovered codes, %d newly covered",
        len(uncovered_codes), sina_new,
    )
    return sina_new


def _collect_fallback(
    existing: dict[str, dict[str, str]],
    uncovered_codes: list[str],
    now_str: str,
    cooldown: float,
) -> int:
    """Fallback: query each uncovered stock by its code directly via EastMoney.

    Returns the number of newly covered stocks.
    """
    if not uncovered_codes:
        return 0

    logger.info("fallback: querying %d uncovered codes by stock code", len(uncovered_codes))
    fallback_new = 0
    for i, code in enumerate(uncovered_codes):
        if i > 0:
            time.sleep(cooldown)

        try:
            row = _fetch_by_code(code)
        except Exception as exc:
            logger.debug("fallback [%d/%d] %s: ERROR %s", i + 1, len(uncovered_codes), code, exc)
            continue

        if row is None:
            continue

        if code not in existing:
            existing[code] = {**row, "supply_chain": "", "source": "em_datacenter", "updated_at": now_str}
            fallback_new += 1

        if (i + 1) % 50 == 0:
            logger.info(
                "fallback [%d/%d]: %d new (total: %d)",
                i + 1, len(uncovered_codes), fallback_new, len(existing),
            )

    logger.info(
        "fallback complete: %d uncovered codes, %d newly covered",
        len(uncovered_codes), fallback_new,
    )
    return fallback_new


# ——— public API ————————————————————————————————————————————————————

def collect_classification(
    output_path: str | Path,
    *,
    cooldown: float = 1.5,
    snapshot_codes: list[str] | None = None,
) -> pd.DataFrame:
    """Collect A-share industry/concept classification and save to CSV.

    Queries each industry individually via the xuangu API to bypass the
    ~500-item per-query limit.  Merges with any existing data at
    *output_path* (incremental mode).

    If *snapshot_codes* is provided, fallback steps query each uncovered
    stock via EastMoney per-code API and then Sina finance, maximising
    coverage against the snapshot.

    The output CSV always contains columns: code, industry, concepts,
    source, updated_at, coverage_status.

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

    # Fallback: query uncovered codes by stock code directly (EastMoney)
    fallback_new = 0
    sina_new = 0
    if snapshot_codes:
        uncovered = sorted(set(snapshot_codes) - set(existing.keys()))
        if uncovered:
            fallback_new = _collect_fallback(existing, uncovered, now_str, cooldown)
            # Sina fallback for codes still uncovered after EastMoney fallback
            still_uncovered = sorted(
                set(snapshot_codes) - set(existing.keys())
            )
            if still_uncovered:
                sina_new = _collect_sina_fallback(
                    existing, still_uncovered, now_str, cooldown,
                )

    # Build and save DataFrame
    # Ensure all required columns exist by filling missing ones
    df = pd.DataFrame(list(existing.values()))
    # Ensure all _CLASSIFICATION_FIELDS columns exist
    for col in _CLASSIFICATION_FIELDS:
        if col not in df.columns:
            df[col] = ""
    df = df[_CLASSIFICATION_FIELDS]
    df = df.sort_values("code", ignore_index=True)

    # Set coverage_status for all rows
    if snapshot_codes:
        snapshot_set = set(snapshot_codes)
        all_covered = snapshot_set.issubset(set(df["code"].tolist()))
        df["coverage_status"] = "full" if all_covered else "partial"
    else:
        df["coverage_status"] = "full"

    # Always write to output path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")

    industry_filled = (df["industry"] != "").sum()
    concepts_filled = (df["concepts"] != "").sum()
    snapshot_total = len(snapshot_codes) if snapshot_codes else 0
    covered_in_snapshot = len(set(df["code"]) & set(snapshot_codes)) if snapshot_codes else len(df)
    logger.info(
        "classification saved: %d rows (%d per-industry + %d em fallback + %d sina fallback), "
        "industry=%d, concepts=%d, snapshot_codes=%d, coverage=%.1f%%",
        len(df), new_count, fallback_new, sina_new,
        industry_filled, concepts_filled,
        snapshot_total,
        (covered_in_snapshot / snapshot_total * 100) if snapshot_total else 100.0,
    )
    return df


def load_classification(path: str | Path) -> pd.DataFrame:
    """Load classification_source.csv, skipping comment lines."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Classification file not found: {path}")
    return pd.read_csv(path, dtype=str, encoding="utf-8", keep_default_na=False, comment="#")


# ——— supply chain map ——————————————————————————————————————————————

def collect_supply_chain(
    output_path: str | Path,
    *,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """Create or update supply_chain_map.csv with the correct schema.

    Columns: theme, code, role, relation_type, evidence_source, updated_at

    *relation_type* is one of:
      - "official_disclosure": from official company disclosure (e.g., annual report)
      - "supply_chain_inference": inferred from supply chain logic or third-party data

    If no supply chain data is available from external sources, this function
    writes an empty file with the correct headers, ensuring the schema is
    always present and stable.

    Returns the supply chain DataFrame (may be empty).
    """
    output_path = Path(output_path)
    now_str = datetime.now(timezone.utc).isoformat()

    # Load existing data if any
    existing_rows: list[dict[str, str]] = []
    if output_path.is_file():
        try:
            df_existing = pd.read_csv(output_path, dtype=str, keep_default_na=False)
            df_existing = df_existing[~df_existing["code"].str.startswith("#", na=False)]
            existing_rows = df_existing.to_dict(orient="records")
            logger.info(
                "loaded %d existing supply chain rows from %s",
                len(existing_rows), output_path,
            )
        except Exception as exc:
            logger.warning("could not load existing %s: %s", output_path, exc)

    # Attempt to collect supply chain data from EastMoney
    new_rows: list[dict[str, str]] = []
    try:
        new_rows = _collect_supply_chain_from_em(codes, now_str)
    except Exception as exc:
        logger.info("supply chain collection from EastMoney: %s", exc)

    # Merge: existing data takes priority, new data fills gaps
    existing_codes = {r["code"] for r in existing_rows if r.get("code")}
    for row in new_rows:
        if row.get("code") and row["code"] not in existing_codes:
            existing_rows.append(row)
            existing_codes.add(row["code"])

    # Build DataFrame with correct schema
    if existing_rows:
        df = pd.DataFrame(existing_rows)
        for col in _SUPPLY_CHAIN_FIELDS:
            if col not in df.columns:
                df[col] = ""
        df = df[_SUPPLY_CHAIN_FIELDS]
    else:
        df = pd.DataFrame(columns=_SUPPLY_CHAIN_FIELDS)

    df = df.sort_values(["theme", "code"], ignore_index=True).fillna("")

    # Always write to output path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")

    logger.info(
        "supply_chain_map saved: %d rows to %s",
        len(df), output_path,
    )
    return df


def _collect_supply_chain_from_em(
    codes: list[str] | None,
    now_str: str,
) -> list[dict[str, str]]:
    """Attempt to collect supply chain data from EastMoney.

    EastMoney does not provide a direct supply chain API.  This function
    attempts to query the xuangu API for concept-related data that may
    indicate supply chain relationships.  Since the source does not
    distinguish between official disclosure and inference, all entries
    default to "supply_chain_inference".

    Returns a list of supply chain row dicts.
    """
    if not codes:
        return []

    rows: list[dict[str, str]] = []
    # Try to fetch concept board data that may indicate supply chain themes
    try:
        resp = _eastmoney_get(_PUSH2_URL, params={
            "pn": "1", "pz": "200", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:90+t:3", "fields": "f12,f14",
        }, headers=_HEADERS, timeout=30)
        data = resp.json()
        if data.get("data") and data["data"].get("diff"):
            concept_boards = [
                it["f14"] for it in data["data"]["diff"] if it.get("f14")
            ]
            logger.info(
                "supply chain: found %d concept boards from EastMoney",
                len(concept_boards),
            )
    except Exception as exc:
        logger.debug("supply chain concept board query: %s", exc)
        return rows

    # For each concept board, query stocks that belong to it
    # This establishes theme→code relationships
    for board_name in concept_boards[:50]:  # limit to avoid excessive requests
        try:
            resp = _eastmoney_get(_EM_DATACENTER_URL, params={
                "st": "SECURITY_CODE", "sr": "1", "ps": "500", "p": "1",
                "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,CONCEPT",
                "filter": f'(CONCEPT+in+("{board_name}"))',
                "source": "SELECT_SECURITIES", "client": "WEB",
            }, headers=_HEADERS, timeout=30)
            data = resp.json()
            items = data.get("result", {}).get("data", [])
            for item in items:
                code = str(item.get("SECURITY_CODE", "")).strip()
                if not code or len(code) != 6:
                    continue
                if codes and code not in codes:
                    continue
                rows.append({
                    "theme": board_name,
                    "code": code,
                    "role": "member",
                    "relation_type": "supply_chain_inference",
                    "evidence_source": "em_concept_board",
                    "updated_at": now_str,
                })
        except Exception as exc:
            logger.debug("supply chain board [%s]: %s", board_name, exc)
            continue

    logger.info(
        "supply chain: collected %d theme-code rows from %d concept boards",
        len(rows), len(concept_boards),
    )
    return rows
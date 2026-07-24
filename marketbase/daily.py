# -*- coding: utf-8 -*-
"""A-share daily history acquisition, normalization, caching, and source health."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pandas as pd
import requests

from marketbase.source_guard import call_with_timeout, parse_source_timeout_seconds
from marketbase.source_health import SourceHealth

_DAILY_HISTORY_CACHE_VERSION = 1
_DAILY_HISTORY_CACHE_TTL_SECONDS = 24 * 60 * 60
_DAILY_CALL_TIMEOUT_SECONDS = 20.0
_DEFAULT_TUSHARE_HTTP_URL = "http://api.waditu.com"
_BAOSTOCK_LOCK = threading.Lock()
_BAOSTOCK_OUTAGE_ERROR: str | None = None
_source_health = SourceHealth(failure_threshold=3, cooldown_seconds=300)

_thread_local = threading.local()


def _get_http_session() -> requests.Session:
    """Return a thread-local requests.Session with connection pooling."""
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=0,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _thread_local.session = s
    return _thread_local.session






def fetch_daily_history(
    code: str,
    *,
    lookback_days: int = 120,
    source: str = "akshare",
    retries: int = 2,
    cache_dir: str | Path | None = None,
    cache_ttl_seconds: float | None = None,
) -> pd.DataFrame:
    """Fetch daily history for one stock code.

    ``source`` accepts ``tencent``, ``sina``, ``akshare``, ``baostock``,
    ``tushare`` or ``auto``. ``auto`` prefers Tushare when a token is
    configured, then Tencent's direct HTTP K-line endpoint before wrapper-based
    free sources. Without a token it starts with Tencent. Sina is a second
    direct HTTP K-line source before wrapper-based fallbacks.
    """
    normalized_code = _normalize_daily_code(code)
    normalized_lookback_days = int(lookback_days)
    src = _normalize_daily_source(source)
    if src == "auto":
        sources: tuple[str, ...] = (
            ("tushare", "tencent", "sina", "akshare", "baostock")
            if _has_tushare_token()
            else ("tencent", "sina", "akshare", "baostock")
        )
        sources, source_order_notes = _order_daily_sources_by_health(sources)
    elif src in ("akshare", "baostock", "tushare", "tencent", "sina"):
        sources = (src,)
        source_order_notes = []
    else:
        raise ValueError(f"Unsupported daily source: {source}")

    cache_path = None
    if cache_dir is not None:
        cache_path = _daily_history_cache_path(
            cache_dir,
            code=normalized_code,
            source=src,
            lookback_days=normalized_lookback_days,
        )
        cached = _read_daily_history_cache(cache_path, ttl_seconds=cache_ttl_seconds)
        if cached is not None:
            return cached

    attempts = max(int(retries), 0) + 1
    errors: list[str] = []
    for current in sources:
        disabled_reason = _source_health.disabled_reason(current)
        if disabled_reason:
            errors.append(f"{current}: {disabled_reason}")
            continue
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                if current == "tencent":
                    result = _fetch_daily_tencent(
                        normalized_code,
                        lookback_days=normalized_lookback_days,
                    )
                elif current == "sina":
                    result = _fetch_daily_sina(
                        normalized_code,
                        lookback_days=normalized_lookback_days,
                    )
                elif current == "akshare":
                    result = _call_daily_wrapper(
                        _fetch_daily_akshare,
                        current,
                        normalized_code,
                        lookback_days=normalized_lookback_days,
                    )
                elif current == "tushare":
                    result = _call_daily_wrapper(
                        _fetch_daily_tushare,
                        current,
                        normalized_code,
                        lookback_days=normalized_lookback_days,
                    )
                else:
                    result = _call_daily_wrapper(
                        _fetch_daily_baostock,
                        current,
                        normalized_code,
                        lookback_days=normalized_lookback_days,
                    )
                _source_health.record_success(current, rows=len(result))
                result.attrs["daily_source"] = current
                result.attrs["daily_requested_source"] = src
                result.attrs["daily_source_order"] = list(sources)
                result.attrs["daily_source_order_notes"] = list(source_order_notes)
                result.attrs["source_errors"] = list(errors)
                result.attrs["daily_source_health"] = _source_health.snapshot(sources)
                if cache_path is not None:
                    _write_daily_history_cache(
                        cache_path,
                        result,
                        code=normalized_code,
                        source=src,
                        lookback_days=normalized_lookback_days,
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - aggregated below
                last_error = exc
                if attempt >= attempts - 1:
                    break
                time.sleep(min(0.5 * (attempt + 1), 2.0))
        errors.append(f"{current} after {attempts} attempts: {last_error}")
        _source_health.record_failure(current, last_error)

    if cache_path is not None:
        stale = _read_daily_history_cache(
            cache_path,
            ttl_seconds=cache_ttl_seconds,
            allow_stale=True,
        )
        if stale is not None:
            stale.attrs["daily_stale"] = True
            stale.attrs["daily_source_order"] = list(sources)
            stale.attrs["daily_source_order_notes"] = list(source_order_notes)
            stale.attrs["source_errors"] = list(errors)
            stale.attrs["daily_source_health"] = _source_health.snapshot(sources)
            return stale

    raise RuntimeError(
        f"daily history fetch failed for {normalized_code}: {'; '.join(errors)}"
    )


def _normalize_daily_code(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)[-6:]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else text


def _normalize_daily_source(source: str | None) -> str:
    return (source or "akshare").strip().lower()




def _call_daily_wrapper(fetcher, source: str, *args, **kwargs) -> pd.DataFrame:
    return call_with_timeout(
        fetcher,
        *args,
        timeout_sec=_daily_call_timeout_seconds(),
        label=f"daily source {source}",
        **kwargs,
    )


def _daily_call_timeout_seconds() -> float | None:
    return parse_source_timeout_seconds(
        "MARKETBASE_DAILY_CALL_TIMEOUT_SEC",
        default=_DAILY_CALL_TIMEOUT_SECONDS,
    )


def _order_daily_sources_by_health(sources: tuple[str, ...]) -> tuple[tuple[str, ...], list[str]]:
    """Move unhealthy daily sources later while preserving default order ties."""
    return _source_health.order_by_health(sources)


def daily_source_health_snapshot() -> dict[str, dict[str, float | bool | str]]:
    """Return a copy of in-process daily-source health statistics."""
    return _source_health.snapshot()


def _daily_history_cache_path(
    cache_dir: str | Path,
    *,
    code: str,
    source: str,
    lookback_days: int,
) -> Path:
    key = f"{code}|{source}|{int(lookback_days)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    safe_source = "".join(ch if ch.isalnum() else "-" for ch in source).strip("-") or "source"
    safe_code = "".join(ch if ch.isalnum() else "-" for ch in code).strip("-") or "code"
    return Path(cache_dir) / f"{safe_code}_{safe_source}_{int(lookback_days)}_{digest}.json"


def _read_daily_history_cache(
    path: Path,
    *,
    ttl_seconds: float | None,
    allow_stale: bool = False,
) -> pd.DataFrame | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None

    ttl = _DAILY_HISTORY_CACHE_TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
    is_stale = ttl <= 0 or time.time() - stat.st_mtime > ttl
    if is_stale and not allow_stale:
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != _DAILY_HISTORY_CACHE_VERSION:
            return None
        frame = payload.get("frame")
        if not isinstance(frame, dict):
            return None
        columns = frame.get("columns")
        data = frame.get("data")
        if not isinstance(columns, list) or not isinstance(data, list):
            return None
        df = pd.DataFrame(data, columns=columns)
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            for key in ("daily_source", "daily_requested_source", "daily_source_order", "daily_source_order_notes", "source_errors", "daily_source_health"):
                if key in metadata:
                    df.attrs[key] = metadata[key]
        if is_stale:
            df.attrs["daily_stale"] = True
        return df
    except Exception:
        return None


def _write_daily_history_cache(
    path: Path,
    df: pd.DataFrame,
    *,
    code: str,
    source: str,
    lookback_days: int,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _DAILY_HISTORY_CACHE_VERSION,
            "key": {
                "code": code,
                "source": source,
                "lookback_days": int(lookback_days),
            },
            "metadata": {
                "daily_source": df.attrs.get("daily_source", source),
                "daily_requested_source": df.attrs.get("daily_requested_source", source),
                "daily_source_order": list(df.attrs.get("daily_source_order", [])),
                "daily_source_order_notes": list(df.attrs.get("daily_source_order_notes", [])),
                "source_errors": list(df.attrs.get("source_errors", [])),
                "daily_source_health": df.attrs.get("daily_source_health", {}),
            },
            "created_at": datetime.now().isoformat(),
            "frame": json.loads(df.to_json(orient="split", date_format="iso", force_ascii=False)),
        }
        tmp_path = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        return


def _fetch_daily_akshare(code: str, *, lookback_days: int) -> pd.DataFrame:
    import akshare as ak

    start_date = (datetime.now() - timedelta(days=max(lookback_days * 2, 90))).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=str(code).zfill(6),
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if df is None or df.empty:
        raise RuntimeError(f"akshare daily history empty for {code}")
    return df.tail(max(lookback_days, 30)).copy()


def _fetch_daily_tencent(code: str, *, lookback_days: int) -> pd.DataFrame:
    """Fetch forward-adjusted daily history from Tencent's direct HTTP API.

    The endpoint follows a-stock-data's low-friction source pattern for
    stable A-share market data access: no wrapper dependency, browser-like HTTP,
    and much lower IP-ban risk than Eastmoney-heavy endpoints. Tencent returns
    daily K-lines as rows shaped like ``date, open, close, high, low, volume``;
    amount is not always present, so it is exposed as ``NA`` when absent to keep
    the common daily schema stable.
    """
    symbol = _to_tencent_code(code)
    count = max(int(lookback_days), 30)
    response = _get_http_session().get(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": f"{symbol},day,,,{count},qfq"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0", None):
        message = payload.get("msg") if isinstance(payload, dict) else payload
        raise RuntimeError(f"tencent daily API error for {code}: {message}")
    data = payload.get("data") if isinstance(payload, dict) else None
    stock_data = data.get(symbol) if isinstance(data, dict) else None
    if not isinstance(stock_data, dict):
        raise RuntimeError(f"tencent daily history missing payload for {code}")
    rows = stock_data.get("qfqday") or stock_data.get("day") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"tencent daily history empty for {code}")

    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        normalized_rows.append({
            "date": row[0],
            "open": row[1],
            "close": row[2],
            "high": row[3],
            "low": row[4],
            "volume": float(row[5]) * 100 if row[5] else 0.0,  # 手 → 股
            "amount": row[6] if len(row) > 6 else pd.NA,
        })
    if not normalized_rows:
        raise RuntimeError(f"tencent daily history malformed for {code}")
    df = pd.DataFrame(
        normalized_rows,
        columns=["date", "open", "close", "high", "low", "volume", "amount"],
    )
    for col in ("open", "close", "high", "low", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.tail(count).copy()


def _fetch_daily_sina(code: str, *, lookback_days: int) -> pd.DataFrame:
    """Fetch unadjusted daily history from Sina's direct K-line API.

    Sina provides a lightweight non-Eastmoney HTTP fallback for A-share daily
    bars. It does not expose forward-adjusted prices on this endpoint, so it is
    deliberately placed behind Tencent in ``auto`` but ahead of wrapper-heavy
    sources that are more prone to dependency/API drift.
    """
    symbol = _to_tencent_code(code)
    count = max(int(lookback_days), 30)
    response = _get_http_session().get(
        "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData",
        params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": count},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("result", {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"sina daily history empty for {code}")

    rows: list[dict[str, object]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        rows.append({
            "date": row.get("day") or row.get("date"),
            "open": row.get("open"),
            "close": row.get("close"),
            "high": row.get("high"),
            "low": row.get("low"),
            "volume": row.get("volume"),
            "amount": row.get("amount", pd.NA),
        })
    if not rows:
        raise RuntimeError(f"sina daily history malformed for {code}")

    df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume", "amount"])
    if "date" in df.columns:
        df = df.sort_values("date")
    for col in ("open", "close", "high", "low", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.tail(count).copy()


def _fetch_daily_tushare(code: str, *, lookback_days: int) -> pd.DataFrame:
    """Fetch forward-adjusted daily history via Tushare Pro."""
    token = _tushare_token()
    if not token:
        raise RuntimeError("tushare requires TUSHARE_TOKEN")

    import tushare as ts

    pro = ts.pro_api(token)
    _configure_tushare_client(pro, token=token)

    start_date = (datetime.now() - timedelta(days=max(lookback_days * 2, 90))).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    adj = _normalize_tushare_adj(os.getenv("TUSHARE_DAILY_ADJ", "qfq"))
    ts_code = _to_tushare_code(code)
    df = pro.daily(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if df is None or df.empty:
        raise RuntimeError(f"tushare daily history empty for {code}")
    if adj is not None:
        df = _apply_tushare_adjustment(
            df,
            pro=pro,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            adj=adj,
        )

    normalized = _normalize_tushare_daily_frame(df)
    return normalized.tail(max(lookback_days, 30)).copy()


def _tushare_token() -> str:
    return (
        os.getenv("TUSHARE_TOKEN", "").strip()
        or os.getenv("TUSHARE_API_TOKEN", "").strip()
    )


def _has_tushare_token() -> bool:
    return bool(_tushare_token())


def _configure_tushare_client(pro: object, *, token: str) -> None:
    try:
        setattr(pro, "_DataApi__token", token)
    except Exception:
        pass

    http_url = (
        os.getenv("TUSHARE_API_URL", "").strip()
        or os.getenv("TUSHARE_HTTP_URL", "").strip()
        or _DEFAULT_TUSHARE_HTTP_URL
    )
    try:
        setattr(pro, "_DataApi__http_url", http_url)
    except Exception:
        pass


def _normalize_tushare_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "trade_date": "date",
        "vol": "volume",
    }
    normalized = df.rename(columns=rename_map).copy()
    if "date" in normalized.columns:
        normalized["date"] = normalized["date"].astype(str)
        normalized = normalized.sort_values("date")
    return normalized


def _apply_tushare_adjustment(
    df: pd.DataFrame,
    *,
    pro: object,
    ts_code: str,
    start_date: str,
    end_date: str,
    adj: str,
) -> pd.DataFrame:
    factors = pro.adj_factor(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="trade_date,adj_factor",
    )
    if factors is None or factors.empty:
        raise RuntimeError(f"tushare adj_factor empty for {ts_code}")

    merged = df.merge(factors, on="trade_date", how="left")
    merged = merged.sort_values("trade_date")
    merged["adj_factor"] = pd.to_numeric(merged["adj_factor"], errors="coerce").bfill()
    valid_factors = pd.to_numeric(factors["adj_factor"], errors="coerce").dropna()
    if valid_factors.empty:
        raise RuntimeError(f"tushare adj_factor invalid for {ts_code}")
    latest_factor = float(valid_factors.iloc[0])
    for col in ("open", "high", "low", "close"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        if adj == "hfq":
            merged[col] = merged[col] * merged["adj_factor"]
        else:
            merged[col] = merged[col] * merged["adj_factor"] / latest_factor
    return merged.drop(columns=["adj_factor"])


def _normalize_tushare_adj(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if text in {"", "none", "null", "no", "false", "0"}:
        return None
    if text not in {"qfq", "hfq"}:
        raise RuntimeError(f"unsupported TUSHARE_DAILY_ADJ: {value}")
    return text


def _fetch_daily_baostock(code: str, *, lookback_days: int) -> pd.DataFrame:
    """Fetch daily history via Baostock as a free fallback source.

    Baostock uses ``sh.600519`` / ``sz.000001`` style codes and exposes
    forward-adjusted prices via ``adjustflag='2'``.
    """
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("baostock not installed; pip install baostock") from exc

    global _BAOSTOCK_OUTAGE_ERROR
    if _BAOSTOCK_OUTAGE_ERROR is not None:
        raise RuntimeError(_BAOSTOCK_OUTAGE_ERROR)

    bs_code = _to_baostock_code(code)
    start_date = (datetime.now() - timedelta(days=max(lookback_days * 2, 90))).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    with _BAOSTOCK_LOCK:
        if _BAOSTOCK_OUTAGE_ERROR is not None:
            raise RuntimeError(_BAOSTOCK_OUTAGE_ERROR)

        login_result = bs.login()
        try:
            login_error_code = str(getattr(login_result, "error_code", "0"))
            if login_error_code not in {"", "0"}:
                login_error_msg = getattr(login_result, "error_msg", "")
                raise RuntimeError(f"baostock login error {login_error_code}: {login_error_msg}")

            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != "0":
                message = f"baostock error {rs.error_code}: {rs.error_msg}"
                if _is_baostock_network_outage(rs.error_code, rs.error_msg):
                    _BAOSTOCK_OUTAGE_ERROR = message
                raise RuntimeError(message)
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    if not rows:
        raise RuntimeError(f"baostock daily history empty for {code}")

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    return df.tail(max(lookback_days, 30)).copy()


def _to_baostock_code(code: str) -> str:
    raw = str(code).strip().zfill(6)
    if raw.startswith(("4", "8", "920")):
        raise ValueError(f"Baostock does not support BSE code: {raw}")
    if raw.startswith(("6", "9", "5")):
        return f"sh.{raw}"
    return f"sz.{raw}"


def _to_tushare_code(code: str) -> str:
    raw = str(code).strip().zfill(6)
    if raw.startswith(("4", "8", "920")):
        return f"{raw}.BJ"
    if raw.startswith(("6", "9", "5")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _to_tencent_code(code: str) -> str:
    raw = str(code).strip().zfill(6)
    if raw.startswith(("4", "8", "920")):
        return f"bj{raw}"
    if raw.startswith(("6", "9", "5")):
        return f"sh{raw}"
    return f"sz{raw}"


def _is_baostock_network_outage(error_code: object, error_msg: object) -> bool:
    code = str(error_code)
    message = str(error_msg)
    return code in {"10002007"} or "利大" in message or "俊辺" in message

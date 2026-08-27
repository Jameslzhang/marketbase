# -*- coding: utf-8 -*-
"""实时行情窗口后端逻辑（客观数据，不含建议/排名/交易结论）。

为“指定股票实时行情查看”窗口提供四类能力：

1. 定向实时行情采集：qt.gtimg.cn 链路，沪深与北交所分别独立请求
   （北交所使用独立采集链路），单只或单市场失败不影响其他结果；
2. 本地证券主表搜索联想：名称 / 六位代码 / 拼音首字母（含“北”标识）；
3. 分时分钟数据：腾讯分时接口，响应含累计成交量与累计成交额，
   可客观计算均价线；
4. A 股交易时段状态：盘前 / 盘中 / 午间休市 / 已收盘 / 休市。

本模块不含 HTTP 与界面逻辑；窗口服务入口见项目根目录
``realtime_quote_server.py``，界面见 ``tools/realtime_quote_window.html``。
"""

from __future__ import annotations

import re
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

CN_TZ = timezone(timedelta(hours=8))

MARKET_LABELS = {"sh": "沪", "sz": "深", "bj": "北"}

_STATUS_LABELS = {"active": "正常交易", "suspended": "停牌"}

_QUOTE_BATCH_SIZE = 60
_QUOTE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

_SYMBOL_RE = re.compile(r'v_(sh|sz|bj)(\d{6})="([^"]*)";')
_MARKET_PREFIX_RE = re.compile(r"^(?:sh|sz|bj)", re.IGNORECASE)
_EXCHANGE_SUFFIX_RE = re.compile(r"\.(?:SH|SZ|BJ)$", re.IGNORECASE)


# ── 代码解析 ─────────────────────────────────────────────────────


def market_of_code(code: str) -> str | None:
    """按六位代码推断市场：沪 / 深 / 北。"""
    normalized = str(code).strip().zfill(6)
    if normalized.startswith("6"):
        return "sh"
    if normalized.startswith(("0", "3")):
        return "sz"
    if normalized.startswith(("4", "8", "9")):
        return "bj"
    return None


def normalize_code(value: str) -> str | None:
    """把 603986 / sh603986 / 603986.SH 等形式统一为六位代码，非法返回 None。"""
    text = str(value).strip()
    text = _EXCHANGE_SUFFIX_RE.sub("", text)
    text = _MARKET_PREFIX_RE.sub("", text)
    if not text.isdigit() or not 1 <= len(text) <= 6:
        return None
    return text.zfill(6)


def parse_window_codes(value: str) -> list[str]:
    """解析逗号/分号/空格分隔的代码串，去重并保持用户输入顺序。"""
    parts = re.split(r"[,;，；\s]+", str(value or "").strip())
    codes: list[str] = []
    seen: set[str] = set()
    for part in parts:
        code = normalize_code(part)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def symbol_of(code: str) -> str:
    """六位代码 → 带市场前缀的行情符号（如 sh603986 / bj430047）。"""
    market = market_of_code(code)
    if market is None:
        raise ValueError(f"unsupported A-share code: {code}")
    return f"{market}{str(code).zfill(6)}"


# ── 定向实时行情（腾讯链路，沪深/北独立请求） ─────────────────────


def _parse_quote_fields(code: str, market: str, body: str, observed_at: str) -> dict | None:
    """解析 qt.gtimg.cn 单条行情体（~ 分隔字段）。"""
    fields = body.split("~")
    if len(fields) < 38:
        return None

    def number(index: int) -> float:
        try:
            return float(fields[index])
        except (TypeError, ValueError, IndexError):
            return 0.0

    price = number(3)
    volume_lots = number(36)
    amount = 0.0
    combined = fields[35].split("/") if len(fields) > 35 else []
    if len(combined) >= 3:
        try:
            amount = float(combined[2])
        except ValueError:
            amount = 0.0
    if amount <= 0:
        amount = number(37) * 10_000.0  # 万元 → 元

    raw_time = fields[30].strip() if len(fields) > 30 else ""
    quote_time = ""
    if len(raw_time) == 14 and raw_time.isdigit():
        quote_time = (
            f"{raw_time[0:4]}-{raw_time[4:6]}-{raw_time[6:8]} "
            f"{raw_time[8:10]}:{raw_time[10:12]}:{raw_time[12:14]}"
        )

    return {
        "code": code,
        "name": fields[1].strip() if len(fields) > 1 else "",
        "market": market,
        "market_label": MARKET_LABELS.get(market, ""),
        "price": price,
        "pre_close": number(4),
        "open": number(5),
        "high": number(33),
        "low": number(34),
        "change_pct": number(32),
        "volume": volume_lots * 100.0,  # 手 → 股
        "amount": amount,
        "turnover_rate": number(38),
        "quote_time": quote_time,
        "observed_at": observed_at,
        "source": "tencent_bse" if market == "bj" else "tencent",
    }


def parse_tencent_quote_text(text: str, observed_at: str) -> dict[str, dict]:
    """解析 qt.gtimg.cn 批量响应文本，返回 {六位代码: 行情记录}。"""
    quotes: dict[str, dict] = {}
    for match in _SYMBOL_RE.finditer(text):
        market, code, body = match.groups()
        row = _parse_quote_fields(code, market, body, observed_at)
        if row is not None:
            quotes[code] = row
    return quotes


def fetch_tencent_quotes(
    codes: Iterable[str],
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
    attempts: int = 2,
) -> tuple[pd.DataFrame, list[str]]:
    """按代码定向采集实时行情；沪深与北交所各自独立请求。

    返回 (行情 DataFrame, 错误列表)。某市场批次失败只影响该批次代码，
    未返回的代码在调用方标记为“未找到”。
    """
    wanted: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []
    for code in codes:
        normalized = normalize_code(code)
        if not normalized:
            errors.append(f"无效代码: {code}")
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        wanted.append(normalized)

    groups: dict[str, list[str]] = {}
    for code in wanted:
        market = market_of_code(code)
        if market is None:
            errors.append(f"不支持的市场代码: {code}")
            continue
        groups.setdefault(market, []).append(code)

    sess = session or requests.Session()
    observed_at = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict] = []

    for market in ("sh", "sz", "bj"):  # 北交所独立批次 = 独立采集链路
        group = groups.get(market) or []
        for offset in range(0, len(group), _QUOTE_BATCH_SIZE):
            batch = group[offset : offset + _QUOTE_BATCH_SIZE]
            symbols = ",".join(symbol_of(code) for code in batch)
            text = ""
            for attempt in range(1, max(1, attempts) + 1):
                try:
                    response = sess.get(
                        "https://qt.gtimg.cn/q=" + symbols,
                        headers=_QUOTE_HEADERS,
                        timeout=(10, timeout),
                    )
                    response.raise_for_status()
                    text = response.content.decode("gb18030", errors="ignore")
                    break
                except Exception as exc:  # noqa: BLE001 - 记录批次失败并重试。
                    if attempt >= max(1, attempts):
                        errors.append(f"{market} 批次请求失败: {exc}")
            if not text:
                continue
            rows.extend(parse_tencent_quote_text(text, observed_at).values())

    frame = pd.DataFrame(rows)
    return frame, errors


def query_realtime_quotes(codes: Iterable[str], **fetch_kwargs) -> dict:
    """重新发起一次定向行情采集，按输入顺序返回结果（含北交所覆盖统计）。"""
    requested = parse_window_codes(",".join(codes)) if isinstance(codes, str) else list(codes)
    frame, errors = fetch_tencent_quotes(requested, **fetch_kwargs)
    fetched = (
        frame.drop_duplicates("code", keep="last").set_index("code")
        if not frame.empty and "code" in frame.columns
        else pd.DataFrame()
    )

    bj_codes = [code for code in requested if market_of_code(code) == "bj"]
    bj_actual = 0
    quotes: list[dict] = []
    for code in requested:
        if not fetched.empty and code in fetched.index:
            row = dict(fetched.loc[code])
            if float(row.get("price") or 0) <= 0 and float(row.get("volume") or 0) <= 0:
                row["realtime_status"] = "无成交"
            else:
                row["realtime_status"] = "已获取"
            if row.get("market") == "bj":
                bj_actual += 1
        else:
            market = market_of_code(code) or ""
            row = {
                "code": code,
                "name": "",
                "market": market,
                "market_label": MARKET_LABELS.get(market, ""),
                "price": None,
                "change_pct": None,
                "volume": None,
                "amount": None,
                "quote_time": "",
                "observed_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "source": "",
                "realtime_status": "未找到",
            }
        row["code"] = code
        quotes.append(row)

    return {
        "quotes": quotes,
        "bj": {
            "bj_expected": len(bj_codes),
            "bj_actual": bj_actual,
            "bj_missing": len(bj_codes) - bj_actual,
        },
        "errors": errors,
        "fetched_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 证券主表与搜索联想 ─────────────────────────────────────────────


def master_path(data_root: str | Path) -> Path:
    return Path(data_root) / "daily_runs" / "cache" / "security_master.csv"


def load_master(data_root: str | Path) -> tuple[pd.DataFrame, dict]:
    """读取本地证券主表；返回 (DataFrame, 元信息)。不存在时返回空表。"""
    path = master_path(data_root)
    meta = {
        "available": False,
        "total": 0,
        "bj_count": 0,
        "bj_available": False,
        "updated_at": "",
        "path": str(path),
    }
    if not path.is_file():
        return pd.DataFrame(), meta
    frame = pd.read_csv(path, dtype=str)
    if "code" not in frame.columns:
        return pd.DataFrame(), meta
    frame["code"] = frame["code"].fillna("").str.strip().str.zfill(6)
    frame = frame[frame["code"].str.fullmatch(r"\d{6}")]
    if "market" not in frame.columns:
        frame["market"] = frame["code"].map(lambda c: market_of_code(c) or "")
    meta.update(
        {
            "available": not frame.empty,
            "total": int(len(frame)),
            "bj_count": int((frame["market"] == "bj").sum()),
            "bj_available": bool((frame["market"] == "bj").any()),
            "updated_at": str(frame["updated_at"].max()) if "updated_at" in frame.columns else "",
        }
    )
    return frame.reset_index(drop=True), meta


_pinyin_module = None
_pinyin_cache: dict[str, str] = {}


def pinyin_initials(text: str) -> str:
    """中文名称 → 拼音首字母串（如 兆易创新 → zycx）。pypinyin 缺失返回空串。"""
    global _pinyin_module
    text = str(text or "").strip()
    if not text:
        return ""
    if text in _pinyin_cache:
        return _pinyin_cache[text]
    if _pinyin_module is None:
        try:
            import pypinyin as module  # noqa: PLC0415 - 可选依赖，惰性导入。
        except ImportError:
            _pinyin_cache[text] = ""
            return ""
        _pinyin_module = module
    letters = _pinyin_module.lazy_pinyin(text, style=_pinyin_module.Style.FIRST_LETTER)
    result = "".join(letters).lower()
    _pinyin_cache[text] = result
    return result


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(str(status or "").strip(), "暂时无行情")


def search_securities(
    query: str,
    master: pd.DataFrame,
    *,
    exclude: Iterable[str] = (),
    limit: int = 10,
) -> list[dict]:
    """本地证券主表联想搜索：名称 / 代码 / 拼音首字母，按匹配度排序。

    返回不超过 *limit* 条记录；已添加代码（*exclude*）不出现在结果中。
    """
    text = str(query or "").strip()
    if not text or master.empty:
        return []
    excluded = {normalize_code(code) for code in exclude if normalize_code(code)}
    results: list[tuple[int, str, dict]] = []
    digit_query = text.isdigit()
    padded = text.zfill(6) if digit_query else ""
    pinyin_query = re.sub(r"\s+", "", text.lower())

    for record in master.to_dict("records"):
        code = str(record.get("code", ""))
        name = str(record.get("name", ""))
        if code in excluded:
            continue
        score = 0
        if digit_query:
            if code == padded:
                score = 100
            elif code.startswith(text):
                score = 85
            elif padded and code.endswith(padded[-4:]):
                score = 30
        else:
            lower_name = name.lower()
            if lower_name == text.lower():
                score = 95
            elif lower_name.startswith(text.lower()):
                score = 80
            elif text.lower() in lower_name:
                score = 60
            if score == 0 and pinyin_query.isascii() and pinyin_query:
                initials = pinyin_initials(name)
                if initials and initials.startswith(pinyin_query):
                    score = 50
                elif initials and pinyin_query in initials:
                    score = 30
        if score > 0:
            market = str(record.get("market") or market_of_code(code) or "")
            results.append(
                (
                    score,
                    code,
                    {
                        "code": code,
                        "name": name,
                        "market": market,
                        "market_label": MARKET_LABELS.get(market, ""),
                        "status": str(record.get("status", "")),
                        "status_label": status_label(record.get("status", "")),
                    },
                )
            )

    results.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in results[: max(1, limit)]]


# ── 分时数据 ─────────────────────────────────────────────────────


def parse_minute_rows(rows: Iterable[str]) -> dict:
    """解析腾讯分时行 "HHMM 价格 累计成交量(手) 累计成交额(元)"。

    均价线 = 累计成交额 / (累计成交量 × 100)，为客观计算，不含任何预测。
    """
    times: list[str] = []
    prices: list[float] = []
    avg_prices: list[float | None] = []
    volumes: list[float] = []
    previous_cum = 0.0
    for item in rows:
        parts = str(item).split()
        if len(parts) < 3:
            continue
        raw_time = parts[0].zfill(4)
        try:
            price = float(parts[1])
            cum_volume = float(parts[2])
            cum_amount = float(parts[3]) if len(parts) >= 4 else 0.0
        except ValueError:
            continue
        times.append(f"{raw_time[:2]}:{raw_time[2:]}")
        prices.append(price)
        if cum_volume > 0 and cum_amount > 0:
            avg_prices.append(round(cum_amount / (cum_volume * 100.0), 4))
        else:
            avg_prices.append(None)
        volumes.append(max(cum_volume - previous_cum, 0.0) * 100.0)  # 手 → 股
        previous_cum = cum_volume
    return {"times": times, "prices": prices, "avg_prices": avg_prices, "volumes": volumes}


def fetch_minute_series(
    code: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> dict:
    """获取单只股票当日分时序列（含昨收、名称，用于分时图与摘要）。"""
    from marketbase.live_workflow import fetch_tencent_minute_rows  # noqa: PLC0415

    normalized = normalize_code(code)
    if not normalized:
        raise ValueError(f"unsupported A-share code: {code}")
    rows = fetch_tencent_minute_rows(normalized, timeout=timeout)
    series = parse_minute_rows(rows)
    quote_frame, _errors = fetch_tencent_quotes([normalized], session=session, timeout=timeout)
    name, pre_close = "", None
    if not quote_frame.empty:
        record = quote_frame.iloc[0]
        name = str(record.get("name", ""))
        pre_close = float(record.get("pre_close") or 0) or None
    return {
        "code": normalized,
        "name": name,
        "market": market_of_code(normalized) or "",
        "pre_close": pre_close,
        **series,
    }


# ── 交易时段状态 ─────────────────────────────────────────────────


def market_session(now: datetime | None = None) -> dict:
    """A 股时段状态：盘前 / 盘中 / 午间休市 / 已收盘 / 休市（节假日与周末）。"""
    from marketbase.trade_calendar import is_trading_day  # noqa: PLC0415

    moment = now or datetime.now(CN_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CN_TZ)
    local = moment.astimezone(CN_TZ)
    trading_day = bool(is_trading_day(local))
    clock = local.time()
    if not trading_day:
        phase = "休市"
    elif clock < clock_time(9, 30):
        phase = "盘前"
    elif clock <= clock_time(11, 30):
        phase = "盘中"
    elif clock < clock_time(13, 0):
        phase = "午间休市"
    elif clock <= clock_time(15, 0):
        phase = "盘中"
    else:
        phase = "已收盘"
    return {
        "trading_day": trading_day,
        "phase": phase,
        "clock": local.strftime("%H:%M"),
        "date": local.strftime("%Y-%m-%d"),
    }

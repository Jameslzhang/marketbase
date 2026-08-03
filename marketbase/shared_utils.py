# -*- coding: utf-8 -*-
"""公共工具函数 —— 原子替换、文本中性化、重试、Tushare 配置等."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd

# ---------------------------------------------------------------------------
# 原子文件操作
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _atomic_replace(temp_path: Path, target_path: Path, *, retries: int = 5, delay: float = 0.1) -> None:
    """原子替换目标文件，Windows 锁冲突时指数退避重试."""
    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def _ensure_dir(path: Path) -> None:
    """确保目标文件所在目录存在."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """原子化写入 JSON 文件（临时文件 + 原子替换）."""
    from uuid import uuid4

    _ensure_dir(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _serialize_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    """将 DataFrame 序列化为 orient='split' 的 JSON 列表."""
    return json.loads(df.to_json(orient="split", date_format="iso", force_ascii=False))


# ---------------------------------------------------------------------------
# 文本中性化
# ---------------------------------------------------------------------------

# 统一的禁止词正则（中文 + 英文）
_FORBIDDEN_TERMS = re.compile(
    r"candidate|recommend|buy|sell|signal|score|rank|probability|"
    r"候选|推荐|买入|卖出|信号|评分|排名|概率",
    re.IGNORECASE,
)


def _neutral_error(message: str, *, replacement: str = "data") -> str:
    """用中性词替换错误消息中的禁止词，确保输出不包含投资建议倾向."""
    return _FORBIDDEN_TERMS.sub(replacement, str(message).strip())


# ---------------------------------------------------------------------------
# 重试工具
# ---------------------------------------------------------------------------


def retry_call(
    func: Callable[..., _T],
    *args: Any,
    max_attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> _T:
    """通用重试调用，指数退避."""
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except exceptions as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay * (backoff ** attempt))
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Tushare 客户端配置（共享，避免 daily/snapshot 各自维护副本）
# ---------------------------------------------------------------------------

_DEFAULT_TUSHARE_HTTP_URL = "http://api.waditu.com"


def _configure_tushare_client(pro: object, *, token: str) -> None:
    """配置 Tushare pro 实例的 token 和 HTTP URL."""
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
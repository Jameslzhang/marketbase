"""进度条渲染 —— 从 local_workflow.py 提取."""

from __future__ import annotations

from datetime import datetime
import sys

_BAR_WIDTH = 30
_PROGRESS_STATE: dict[str, int] = {"last_bar_len": 0}


def _ts() -> str:
    """短时间戳 HH:MM:SS."""
    return datetime.now().strftime("%H:%M:%S")


def _render_bar(completed: int, total: int, width: int = _BAR_WIDTH) -> str:
    """Render a █░ progress bar string."""
    if total <= 0:
        return ""
    ratio = min(completed / total, 1.0)
    filled = int(ratio * width)
    pct = ratio * 100
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {pct:5.1f}% ({completed}/{total})"


def _clear_progress_line() -> None:
    """Erase the last progress line from the terminal."""
    last_len = int(_PROGRESS_STATE.get("last_bar_len", 0))
    if last_len > 0 and sys.stdout.isatty():
        try:
            _ = sys.stdout.write("\r" + " " * last_len + "\r")
            _ = sys.stdout.flush()
        except OSError:
            pass
    _PROGRESS_STATE["last_bar_len"] = 0


def _write_progress_line(line: str) -> None:
    """Write or overwrite a progress line (no newline, uses \\r)."""
    _clear_progress_line()
    if sys.stdout.isatty():
        try:
            _ = sys.stdout.write(line)
            _ = sys.stdout.flush()
            _PROGRESS_STATE["last_bar_len"] = len(line)
        except OSError:
            # Windows 终端可能不支持某些 Unicode 字符，降级为 ASCII
            safe = line.encode("ascii", errors="replace").decode("ascii")
            try:
                _ = sys.stdout.write(safe)
                _ = sys.stdout.flush()
                _PROGRESS_STATE["last_bar_len"] = len(safe)
            except OSError:
                pass
    else:
        try:
            print(line, flush=True)
        except OSError:
            safe = line.encode("ascii", errors="replace").decode("ascii")
            print(safe, flush=True)
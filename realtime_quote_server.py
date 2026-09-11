#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""实时行情窗口本地服务（指定股票实时行情查看）。

用法：
    .venv\Scripts\python.exe realtime_quote_server.py            # 默认启动原生桌面小窗
    .venv\Scripts\python.exe realtime_quote_server.py --browser  # 浏览器兜底模式
    .venv\Scripts\python.exe realtime_quote_server.py --port 8899

接口：
    GET /                    窗口界面（tools/realtime_quote_window.html）
    GET /api/status          版本、交易时段、证券主表可用性
    GET /api/quotes?codes=   重新发起一次定向实时行情采集（含北交所覆盖统计）
    GET /api/search?q=       本地证券主表联想搜索（名称/代码/拼音首字母）
    GET /api/minute?code=    单只股票当日分时序列（含昨收与均价线数据）

仅使用标准库 HTTP 服务；行情采集复用 marketbase.realtime_window。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MB_ROOT = Path(__file__).resolve().parent  # 脚本位于 marketbase 项目根目录
if str(MB_ROOT) not in sys.path:
    sys.path.insert(0, str(MB_ROOT))

from marketbase import realtime_window as rw  # noqa: E402

WINDOW_VERSION = "V1.0.0"
HTML_PATH = MB_ROOT / "tools" / "realtime_quote_window.html"

_quotes_lock = threading.Lock()  # 禁止并发刷新（需求 8.1）


def _session_info() -> dict:
    return rw.market_session()


def _master_info(data_root: Path) -> dict:
    _frame, meta = rw.load_master(data_root)
    return meta


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数；默认使用无地址栏的原生桌面小窗。"""
    parser = argparse.ArgumentParser(description="MarketBase 实时行情窗口服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--browser",
        "--open",
        dest="browser",
        action="store_true",
        help="使用浏览器打开页面（排障兜底；--open 为兼容别名）",
    )
    parser.add_argument("--data-root", type=Path, default=MB_ROOT / "data")
    return parser


class DesktopWindowApi:
    """供页面标题栏调用的原生窗口控制接口。"""

    def __init__(self, native_window=None) -> None:
        self.native_window = native_window

    def window_action(self, action: str) -> None:
        if self.native_window is None:
            return
        if action == "minimize":
            self.native_window.minimize()
        elif action == "close":
            self.native_window.destroy()
        else:
            raise ValueError(f"不支持的窗口操作: {action}")


def launch_desktop_window(url: str, *, webview_module=None) -> None:
    """用 pywebview 承载本地页面，呈现为独立的行情小窗。"""
    if webview_module is None:
        try:
            import webview as webview_module  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "未安装桌面窗口依赖。请运行：.venv\\Scripts\\python.exe -m pip install pywebview"
            ) from exc
    api = DesktopWindowApi()
    api.native_window = webview_module.create_window(
        "MarketBase 实时行情",
        url,
        width=520,
        height=760,
        min_size=(420, 560),
        resizable=True,
        frameless=True,
        easy_drag=True,
        js_api=api,
    )
    webview_module.start()


def run_desktop_window(http_server, url: str, *, webview_module=None) -> None:
    """后台运行本地 HTTP 服务；桌面窗口关闭后同步停止服务。"""
    worker = threading.Thread(target=http_server.serve_forever, daemon=True)
    worker.start()
    try:
        launch_desktop_window(url, webview_module=webview_module)
    finally:
        http_server.shutdown()
        http_server.server_close()
        worker.join(timeout=3)


class WindowHandler(BaseHTTPRequestHandler):
    server_version = "MarketBaseRealtimeWindow/" + WINDOW_VERSION
    data_root: Path = MB_ROOT / "data"

    def log_message(self, fmt: str, *args: object) -> None:  # 静默访问日志
        pass

    # ── 输出辅助 ──────────────────────────────────────────────

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── 路由 ─────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - http.server 接口约定。
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ("/", "/index.html"):
                self._handle_page()
            elif parsed.path == "/api/status":
                self._handle_status()
            elif parsed.path == "/api/quotes":
                self._handle_quotes(query)
            elif parsed.path == "/api/search":
                self._handle_search(query)
            elif parsed.path == "/api/minute":
                self._handle_minute(query)
            else:
                self._send_json({"error": f"未知路径: {parsed.path}"}, status=404)
        except Exception as exc:  # noqa: BLE001 - 以 JSON 暴露失败原因，不冒充成功。
            self._send_json({"error": str(exc)}, status=500)

    def _handle_page(self) -> None:
        if not HTML_PATH.is_file():
            self._send_json({"error": f"界面文件缺失: {HTML_PATH}"}, status=500)
            return
        self._send_html(HTML_PATH.read_text(encoding="utf-8"))

    def _handle_status(self) -> None:
        self._send_json(
            {
                "version": WINDOW_VERSION,
                "session": _session_info(),
                "master": _master_info(self.data_root),
                "pinyin_available": bool(rw.pinyin_initials("测")),
            }
        )

    def _handle_quotes(self, query: dict[str, list[str]]) -> None:
        codes = rw.parse_window_codes((query.get("codes") or [""])[0])
        if not codes:
            self._send_json({"error": "未解析出有效的股票代码"}, status=400)
            return
        if not _quotes_lock.acquire(blocking=False):
            self._send_json({"busy": True, "message": "正在刷新，请稍候"})
            return
        try:
            result = rw.query_realtime_quotes(codes)
        finally:
            _quotes_lock.release()
        result["busy"] = False
        self._send_json(result)

    def _handle_search(self, query: dict[str, list[str]]) -> None:
        text = (query.get("q") or [""])[0]
        exclude = rw.parse_window_codes((query.get("exclude") or [""])[0])
        limit = min(int((query.get("limit") or ["10"])[0] or 10), 20)
        master, meta = rw.load_master(self.data_root)
        if not meta["available"]:
            self._send_json(
                {
                    "results": [],
                    "master": meta,
                    "message": "证券主表暂不可用，请稍后重试",
                }
            )
            return
        results = rw.search_securities(text, master, exclude=exclude, limit=limit)
        self._send_json({"results": results, "master": meta})

    def _handle_minute(self, query: dict[str, list[str]]) -> None:
        code = rw.normalize_code((query.get("code") or [""])[0])
        if not code:
            self._send_json({"error": "无效股票代码"}, status=400)
            return
        series = rw.fetch_minute_series(code)
        self._send_json(series)


def main() -> int:
    args = build_parser().parse_args()

    WindowHandler.data_root = args.data_root
    server = ThreadingHTTPServer((args.host, args.port), WindowHandler)
    url = f"http://{args.host}:{args.port}/"
    if args.browser:
        print(f"实时行情窗口浏览器模式已启动: {url}（版本 {WINDOW_VERSION}，Ctrl+C 退出）")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止")
        finally:
            server.server_close()
        return 0

    print(f"实时行情桌面小窗正在启动（版本 {WINDOW_VERSION}）")
    try:
        run_desktop_window(server, url)
    except KeyboardInterrupt:
        print("\n服务已停止")
    except RuntimeError as exc:
        print(f"桌面小窗无法启动：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

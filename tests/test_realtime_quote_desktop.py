# -*- coding: utf-8 -*-
"""实时行情原生桌面小窗启动逻辑测试。"""

from __future__ import annotations

import realtime_quote_server as server


class _FakeWebview:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, dict]] = []
        self.started = False

    def create_window(self, title: str, url: str, **kwargs: object) -> None:
        self.created.append((title, url, kwargs))

    def start(self) -> None:
        self.started = True


class _FakeHttpServer:
    def __init__(self) -> None:
        self.served = False
        self.shutdown_called = False
        self.closed = False

    def serve_forever(self) -> None:
        self.served = True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def server_close(self) -> None:
        self.closed = True


class _FakeNativeWindow:
    def __init__(self) -> None:
        self.minimized = False
        self.destroyed = False

    def minimize(self) -> None:
        self.minimized = True

    def destroy(self) -> None:
        self.destroyed = True


def test_default_cli_mode_is_desktop_window():
    args = server.build_parser().parse_args([])
    assert args.browser is False


def test_browser_flag_keeps_browser_fallback_available():
    args = server.build_parser().parse_args(["--browser"])
    assert args.browser is True


def test_launch_desktop_window_uses_compact_native_shell():
    fake = _FakeWebview()

    server.launch_desktop_window("http://127.0.0.1:8765/", webview_module=fake)

    assert fake.started is True
    assert len(fake.created) == 1
    title, url, options = fake.created[0]
    assert title == "MarketBase 实时行情"
    assert url == "http://127.0.0.1:8765/"
    assert options["width"] == 520
    assert options["height"] == 760
    assert options["min_size"] == (420, 560)
    assert options["resizable"] is True
    assert options["frameless"] is True
    assert options["easy_drag"] is True


def test_desktop_window_api_controls_native_window():
    native = _FakeNativeWindow()
    api = server.DesktopWindowApi(native)

    api.window_action("minimize")
    assert native.minimized is True
    api.window_action("close")
    assert native.destroyed is True


def test_desktop_window_exit_stops_local_quote_server():
    http_server = _FakeHttpServer()
    webview = _FakeWebview()

    server.run_desktop_window(http_server, "http://127.0.0.1:8765/", webview_module=webview)

    assert http_server.served is True
    assert http_server.shutdown_called is True
    assert http_server.closed is True

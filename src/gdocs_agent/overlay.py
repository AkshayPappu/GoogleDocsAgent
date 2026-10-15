"""Floating always-on-top chat. The button sits above the doc; the page does not."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from gdocs_agent.api import app
from gdocs_agent.overlay_layout import BUG, COLLAPSED, Layout, compute, default_bug

_HOST = "127.0.0.1"
_START_PORT = 8000
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_NOACTIVATE = 0x08000000
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_HWND_TOPMOST = -1
_RGN_OR = 2


def main() -> None:
    """Start the local chat service and open the floating button."""
    if sys.platform != "win32":
        raise SystemExit("The floating chat currently runs on Windows.")
    _dpi_aware()
    port = _free_port()
    server = _serve(port)
    scale = _system_scale()
    areas = _logical_areas(scale)
    bug_x, bug_y = _load_bug(areas)
    layout, bug_x, bug_y = compute(bug_x, bug_y, "bug", areas, False)
    bridge = OverlayBridge(areas, bug_x, bug_y, scale)

    import webview

    window = webview.create_window(
        "Doc chat",
        url=f"http://{_HOST}:{port}/",
        js_api=bridge,
        width=layout.width,
        height=layout.height,
        x=layout.x,
        y=layout.y,
        min_size=(COLLAPSED, COLLAPSED),
        resizable=False,
        frameless=True,
        easy_drag=False,
        shadow=False,
        focus=False,
        on_top=True,
        transparent=False,
        text_select=True,
        zoomable=False,
        draggable=False,
        background_color="#FFFDF8",
    )
    bridge.attach(window)
    window.events.loaded += bridge.on_ready
    try:
        webview.start()
    finally:
        server.should_exit = True


class OverlayBridge:
    """Moves the native window from the page without taking the keyboard."""

    def __init__(
        self,
        areas: list[tuple[int, int, int, int]],
        bug_x: int,
        bug_y: int,
        scale: float,
    ) -> None:
        self._areas = areas
        self._bug_x = bug_x
        self._bug_y = bug_y
        self._window = None
        self._hwnd = 0
        self._scale = scale or 1.0
        self._typing = False
        self._ready = False
        self._pending: Layout | None = None
        self._previous_foreground = 0
        self._lock = threading.Lock()

    def attach(self, window) -> None:
        self._window = window
        self._previous_foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)

    def on_ready(self) -> None:
        # Win32 only. Touching the form here runs off the UI thread and WebView2 rejects it.
        self._hwnd = int(ctypes.windll.user32.FindWindowW(None, "Doc chat") or 0)
        self._pending = None
        self._ready = True
        self._sync_screen()
        threading.Timer(0.5, self._sync_screen).start()
        threading.Timer(0.25, self._restore_previous).start()
        threading.Timer(0.7, self._restore_previous).start()

    def _sync_screen(self) -> None:
        if not self._hwnd:
            self._hwnd = int(ctypes.windll.user32.FindWindowW(None, "Doc chat") or 0)
        if not self._hwnd:
            return
        try:
            scale = _dpi_scale(self._hwnd)
            if scale:
                self._scale = scale
            with self._lock:
                self._areas = _logical_areas(self._scale)
                self._bug_x, self._bug_y = compute(
                    self._bug_x, self._bug_y, "bug", self._areas, False
                )[1:]
                self._style(noactivate=not self._typing)
                self._place(_current_bug_layout(self._bug_x, self._bug_y, self._areas))
        except Exception:
            import traceback

            traceback.print_exc()
            return

    def get_state(self) -> dict:
        return {"bugX": self._bug_x, "bugY": self._bug_y}

    def layout(self, bug_x: float, bug_y: float, mode: str, notice: bool) -> dict:
        with self._lock:
            areas = _logical_areas(self._scale or _system_scale())
            self._areas = areas
            placed, bug_x_i, bug_y_i = compute(bug_x, bug_y, str(mode), areas, bool(notice))
            self._bug_x = bug_x_i
            self._bug_y = bug_y_i
            self._place(placed)
            return _payload(placed, bug_x_i, bug_y_i)

    def save_position(self, bug_x: float, bug_y: float) -> dict:
        with self._lock:
            bug_x_i, bug_y_i = compute(bug_x, bug_y, "bug", self._areas, False)[1:]
            self._bug_x, self._bug_y = bug_x_i, bug_y_i
            _save_bug(bug_x_i, bug_y_i)
            return {"bugX": bug_x_i, "bugY": bug_y_i}

    def reset_position(self) -> dict:
        with self._lock:
            areas = _logical_areas(self._scale or _system_scale())
            self._areas = areas
            bug_x, bug_y = default_bug(areas)
            self._bug_x, self._bug_y = bug_x, bug_y
            _save_bug(bug_x, bug_y)
            placed, _, _ = compute(bug_x, bug_y, "bug", areas, False)
            self._place(placed)
            return _payload(placed, bug_x, bug_y)

    def activate(self) -> None:
        """Let the chat field take keys. Hover and dragging do not call this."""
        self._typing = True
        if not self._hwnd:
            return
        current = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if current and current != self._hwnd:
            self._previous_foreground = current
        self._style(noactivate=False)
        _force_foreground(self._hwnd)

    def deactivate(self) -> None:
        self._typing = False
        if self._hwnd:
            self._style(noactivate=True)
        self._restore_previous()

    def pointer_over(self) -> bool:
        """True when the cursor is still on the button or the open panel."""
        if not self._hwnd:
            return False
        point = _POINT()
        if not _GetCursorPos(ctypes.byref(point)):
            return False
        hit = _WindowFromPoint(point)
        if not hit:
            return False
        root = _GetAncestor(hit, 2)  # GA_ROOT
        return int(root or hit) == int(self._hwnd)

    def quit(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def _restore_previous(self) -> None:
        if self._typing or not self._hwnd or not self._previous_foreground:
            return
        if int(ctypes.windll.user32.GetForegroundWindow() or 0) != self._hwnd:
            return
        _force_foreground(self._previous_foreground)

    def _place(self, layout: Layout) -> None:
        if not self._ready or not self._hwnd:
            self._pending = layout
            return
        scale = self._scale or 1.0
        width = int(round(layout.width * scale))
        height = int(round(layout.height * scale))
        _set_window_pos(
            self._hwnd,
            _HWND_TOPMOST,
            int(round(layout.x * scale)),
            int(round(layout.y * scale)),
            width,
            height,
            _SWP_NOACTIVATE,
        )
        _apply_region(self._hwnd, layout, scale)

    def _style(self, noactivate: bool) -> None:
        if not self._hwnd:
            return
        style = _GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
        style |= _WS_EX_TOOLWINDOW
        style &= ~_WS_EX_APPWINDOW
        if noactivate:
            style |= _WS_EX_NOACTIVATE
        else:
            style &= ~_WS_EX_NOACTIVATE
        _SetWindowLongW(self._hwnd, _GWL_EXSTYLE, style)
        _set_window_pos(
            self._hwnd,
            _HWND_TOPMOST,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )

def _current_bug_layout(bug_x: int, bug_y: int, areas) -> Layout:
    return compute(bug_x, bug_y, "bug", areas, False)[0]


def _payload(layout: Layout, bug_x: int, bug_y: int) -> dict:
    return {
        "x": layout.x,
        "y": layout.y,
        "width": layout.width,
        "height": layout.height,
        "anchor": layout.anchor,
        "vertical": layout.vertical,
        "panelWidth": layout.panel_width,
        "panelHeight": layout.panel_height,
        "showPanel": layout.show_panel,
        "showMenu": layout.show_menu,
        "bugX": bug_x,
        "bugY": bug_y,
    }


def _serve(port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host=_HOST, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, name="gdocs-overlay-http", daemon=True).start()
    _wait_until_up(port)
    return server


def _wait_until_up(port: int) -> None:
    deadline = time.time() + 15
    url = f"http://{_HOST}:{port}/api/auth/status"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.4) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("The chat service did not start.")


def _free_port() -> int:
    for port in range(_START_PORT, _START_PORT + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((_HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port for the doc chat.")


def _state_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "gdocs-agent" if appdata else Path.home() / ".gdocs-agent"
    root.mkdir(parents=True, exist_ok=True)
    return root / "overlay.json"


def _load_bug(areas) -> tuple[int, int]:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return compute(float(data["bug_x"]), float(data["bug_y"]), "bug", areas, False)[1:]
    except (OSError, ValueError, KeyError, TypeError):
        bug = default_bug(areas)
        return bug


def _save_bug(bug_x: int, bug_y: int) -> None:
    _state_path().write_text(
        json.dumps({"bug_x": bug_x, "bug_y": bug_y}),
        encoding="utf-8",
    )


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_GetCursorPos = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(_POINT))(
    ("GetCursorPos", ctypes.windll.user32)
)
_WindowFromPoint = ctypes.WINFUNCTYPE(ctypes.c_void_p, _POINT)(
    ("WindowFromPoint", ctypes.windll.user32)
)
_GetAncestor = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)(
    ("GetAncestor", ctypes.windll.user32)
)
_GetDpiForWindow = ctypes.WINFUNCTYPE(ctypes.c_uint, ctypes.c_void_p)(
    ("GetDpiForWindow", ctypes.windll.user32)
)
_GetDpiForSystem = ctypes.WINFUNCTYPE(ctypes.c_uint)(("GetDpiForSystem", ctypes.windll.user32))
_GetWindowLongW = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int)(
    ("GetWindowLongW", ctypes.windll.user32)
)
_SetWindowLongW = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_long)(
    ("SetWindowLongW", ctypes.windll.user32)
)
_SetWindowPos = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
)(("SetWindowPos", ctypes.windll.user32))


def _set_window_pos(hwnd: int, insert_after: int, x: int, y: int, width: int, height: int, flags: int) -> None:
    _SetWindowPos(
        ctypes.c_void_p(hwnd),
        ctypes.c_void_p(insert_after),
        x,
        y,
        width,
        height,
        flags,
    )


def _dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        return


def _system_scale() -> float:
    try:
        dpi = _GetDpiForSystem()
    except Exception:
        return 1.0
    return (dpi or 96) / 96


def _dpi_scale(hwnd: int) -> float:
    try:
        dpi = _GetDpiForWindow(ctypes.c_void_p(hwnd))
    except Exception:
        return 1.0
    if not dpi:
        return 1.0
    return dpi / 96


def _logical_areas(scale: float) -> list[tuple[int, int, int, int]]:
    """Work areas in the same pixels the page uses for layout."""
    areas = _work_areas()
    if scale <= 1.01 or not _work_area_is_physical():
        return areas
    return [tuple(int(round(value / scale)) for value in area) for area in areas]


def _work_area_is_physical() -> bool:
    import clr

    clr.AddReference("System.Windows.Forms")
    import System.Windows.Forms as WinForms

    bounds = WinForms.Screen.PrimaryScreen.Bounds
    desktop = _desktop_physical_width()
    return bool(desktop) and abs(int(bounds.Width) - desktop) <= 2


def _desktop_physical_width() -> int:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    try:
        return int(gdi32.GetDeviceCaps(hdc, 118) or 0)
    finally:
        user32.ReleaseDC(0, hdc)


def _work_areas() -> list[tuple[int, int, int, int]]:
    import clr

    clr.AddReference("System.Windows.Forms")
    import System.Windows.Forms as WinForms

    primary = None
    others = []
    for screen in WinForms.Screen.AllScreens:
        area = screen.WorkingArea
        rect = (int(area.X), int(area.Y), int(area.Width), int(area.Height))
        if screen.Primary:
            primary = rect
        else:
            others.append(rect)
    if primary:
        return [primary, *others]
    return others or [(0, 0, 1280, 800)]


def _apply_region(hwnd: int, layout: Layout, scale: float) -> None:
    """Clip hit-testing to the button, plus the bar when it is open."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    def px(value: int) -> int:
        return int(round(value * scale))

    bug = gdi32.CreateEllipticRgn(
        px(layout.bug_left),
        px(layout.bug_top),
        px(layout.bug_left + BUG) + 1,
        px(layout.bug_top + BUG) + 1,
    )
    if not layout.show_panel and not layout.show_menu:
        user32.SetWindowRgn(hwnd, bug, True)
        return

    corner = px(32)
    panel = gdi32.CreateRoundRectRgn(
        px(layout.panel_left),
        px(layout.panel_top),
        px(layout.panel_left + layout.panel_width) + 1,
        px(layout.panel_top + layout.panel_height) + 1,
        corner,
        corner,
    )
    gdi32.CombineRgn(panel, panel, bug, _RGN_OR)
    gdi32.DeleteObject(bug)
    user32.SetWindowRgn(hwnd, panel, True)


def _force_foreground(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    this_thread = kernel32.GetCurrentThreadId()
    attached = False
    if foreground_thread and foreground_thread != this_thread:
        attached = bool(user32.AttachThreadInput(this_thread, foreground_thread, True))
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(this_thread, foreground_thread, False)

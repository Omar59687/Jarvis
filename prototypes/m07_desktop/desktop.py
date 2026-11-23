"""Local-first Windows tray companion; no server or repository commands are run."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request

from dataclasses import dataclass
from typing import Callable, Protocol


LOGGER = logging.getLogger(__name__)
DEFAULT_PORT = 8765
DEFAULT_SHORTCUT = "CTRL+ALT+J"
HEALTH_TIMEOUT_SECONDS = 0.75
HEALTH_RESPONSE_LIMIT = 2048
STATUS_POLL_INTERVAL_MS = 15_000


@dataclass(frozen=True)
class Shortcut:
    label: str
    modifiers: int
    virtual_key: int


def parse_shortcut(value: str) -> Shortcut:
    if not isinstance(value, str) or not value.strip() or len(value) > 48:
        raise ValueError("shortcut must contain modifiers and one key")
    parts = [part.strip().upper() for part in value.split("+")]
    if any(not part for part in parts) or len(parts) < 2:
        raise ValueError("shortcut must contain modifiers and one key")
    modifier_values = {"CTRL": 0x0002, "CONTROL": 0x0002, "ALT": 0x0001,
                       "SHIFT": 0x0004, "WIN": 0x0008, "WINDOWS": 0x0008}
    modifiers = 0
    for modifier in parts[:-1]:
        if modifier not in modifier_values or modifiers & modifier_values[modifier]:
            raise ValueError("shortcut has an unknown or repeated modifier")
        modifiers |= modifier_values[modifier]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        virtual_key = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        virtual_key = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError("shortcut key must be A-Z, 0-9, or F1-F24")
    return Shortcut(label="+".join(parts), modifiers=modifiers | 0x4000,
                    virtual_key=virtual_key)


def _open_default_browser(url: str) -> bool:
    if os.name != "nt":
        return False
    os.startfile(url)
    return True

def dashboard_url(port: int, *, voice: bool = False) -> str:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer from 1 to 65535")
    suffix = "#voice-panel" if voice else ""
    return f"http://127.0.0.1:{port}/{suffix}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def backend_is_available(port: int, *, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """Read only the fixed loopback liveness endpoint; never follows redirects."""
    url = dashboard_url(port) + "health"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = response.read(HEALTH_RESPONSE_LIMIT + 1)
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return False
    if len(payload) > HEALTH_RESPONSE_LIMIT:
        return False
    try:
        result = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (isinstance(result, dict) and result.get("status") == "ok"
            and result.get("service") == "m03-b2")


class DesktopHost(Protocol):
    def acquire_single_instance(self) -> bool: ...
    def start(self, companion: DesktopCompanion, shortcut: Shortcut) -> bool: ...
    def update_view(self, status: str, shortcut_available: bool,
                    notifications_enabled: bool) -> None: ...
    def notify_backend_change(self, available: bool) -> None: ...
    def run_message_loop(self, tick: Callable[[], None]) -> None: ...
    def request_exit(self) -> None: ...
    def close(self) -> None: ...


class DesktopCompanion:
    """Owns only this tray process; an existing Jarvis backend is never stopped."""

    def __init__(self, *, port: int, shortcut: Shortcut, notifications_enabled: bool,
                 host: DesktopHost,
                 open_url: Callable[[str], object] | None = None,
                 health_check: Callable[[int], bool] = backend_is_available):
        self.port = port
        self.shortcut = shortcut
        self.notifications_enabled = notifications_enabled
        self.host = host
        self.open_url = open_url or _open_default_browser
        self.health_check = health_check
        self._backend_available: bool | None = None
        self._shortcut_available = False
        self._stopped = threading.Event()
        self._instance_acquired = False

    def run(self) -> str:
        if not self.host.acquire_single_instance():
            return "already_running"
        self._instance_acquired = True
        try:
            self._shortcut_available = self.host.start(self, self.shortcut)
            self.refresh_backend_status()
            self.host.run_message_loop(self.refresh_backend_status)
            return "stopped"
        finally:
            self.close()

    def open_dashboard(self) -> None:
        self.open_url(dashboard_url(self.port))

    def open_voice(self) -> None:
        self.open_url(dashboard_url(self.port, voice=True))

    def toggle_notifications(self) -> None:
        self.notifications_enabled = not self.notifications_enabled
        self._update_view()

    def request_stop(self) -> None:
        if not self._stopped.is_set():
            self._stopped.set()
            self.host.request_exit()

    def refresh_backend_status(self) -> None:
        if self._stopped.is_set():
            return
        available = bool(self.health_check(self.port))
        previous = self._backend_available
        self._backend_available = available
        self._update_view()
        if previous is not None and previous != available and self.notifications_enabled:
            self.host.notify_backend_change(available)

    def _update_view(self) -> None:
        if self._backend_available is None:
            status = "Checking backend"
        else:
            status = "Backend connected" if self._backend_available else "Backend unavailable"
        self.host.update_view(status, self._shortcut_available,
                              self.notifications_enabled)

    def close(self) -> None:
        if self._stopped.is_set() and not self._instance_acquired:
            return
        self._stopped.set()
        try:
            self.host.close()
        finally:
            self._instance_acquired = False


def _win32_types():
    if os.name != "nt":
        raise OSError("the native Jarvis tray companion is available only on Windows")
    from ctypes import wintypes

    DWORD = ctypes.c_uint32
    UINT = ctypes.c_uint32
    LONG = ctypes.c_int32
    ULONG_PTR = ctypes.c_size_t

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", UINT), ("style", UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", DWORD), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class TimeoutOrVersion(ctypes.Union):
        _fields_ = [("uTimeout", UINT), ("uVersion", UINT)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _anonymous_ = ("timeout_or_version",)
        _fields_ = [("cbSize", DWORD), ("hWnd", wintypes.HWND), ("uID", UINT),
                    ("uFlags", UINT), ("uCallbackMessage", UINT),
                    ("hIcon", wintypes.HICON), ("szTip", ctypes.c_wchar * 128),
                    ("dwState", DWORD), ("dwStateMask", DWORD),
                    ("szInfo", ctypes.c_wchar * 256),
                    ("timeout_or_version", TimeoutOrVersion),
                    ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", LONG), ("y", LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", UINT),
                    ("wParam", ULONG_PTR), ("lParam", ctypes.c_ssize_t),
                    ("time", DWORD), ("pt", POINT), ("lPrivate", DWORD)]

    return wintypes, WNDCLASSEXW, NOTIFYICONDATAW, MSG, POINT


class WindowsDesktopHost:
    """Small ctypes adapter over the documented Win32 tray and hotkey APIs."""

    WM_DESTROY = 0x0002
    WM_CONTEXTMENU = 0x007B
    WM_TIMER = 0x0113
    WM_HOTKEY = 0x0312
    WM_TRAY = 0x8001
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIF_INFO = 0x00000010
    NIIF_INFO = 0x00000001
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    MF_CHECKED = 0x0008
    MF_GRAYED = 0x0001
    ERROR_ALREADY_EXISTS = 183
    ERROR_HOTKEY_ALREADY_REGISTERED = 1409
    HOTKEY_ID = 1
    TIMER_ID = 1
    CMD_OPEN = 101
    CMD_VOICE = 102
    CMD_NOTIFICATIONS = 103
    CMD_EXIT = 104

    def __init__(self, *, poll_interval_ms: int = STATUS_POLL_INTERVAL_MS):
        (self.wintypes, self.WNDCLASSEXW, self.NOTIFYICONDATAW,
         self.MSG, self.POINT) = _win32_types()
        if not 1000 <= poll_interval_ms <= 60_000:
            raise ValueError("poll interval must be between one and sixty seconds")
        self.poll_interval_ms = poll_interval_ms
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hwnd = None
        self.mutex = None
        self.icon_added = False
        self.hotkey_registered = False
        self.companion: DesktopCompanion | None = None
        self.shortcut = Shortcut(DEFAULT_SHORTCUT, 0x4003, ord("J"))
        self.shortcut_available = False
        self.backend_status = "Checking backend"
        self.notifications_enabled = False
        self._window_proc = None
        self._tick: Callable[[], None] | None = None
        self._class_name = "JarvisM07TrayWindow"
        self._configure_functions()

    def _configure_functions(self) -> None:
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(self.WNDCLASSEXW)]
        self.user32.RegisterClassExW.restype = self.wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            self.wintypes.DWORD, self.wintypes.LPCWSTR, self.wintypes.LPCWSTR,
            self.wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, self.wintypes.HWND, self.wintypes.HMENU,
            self.wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        self.user32.CreateWindowExW.restype = self.wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [
            self.wintypes.HWND, self.wintypes.UINT, self.wintypes.WPARAM,
            self.wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user32.RegisterHotKey.argtypes = [self.wintypes.HWND, ctypes.c_int,
                                                self.wintypes.UINT, self.wintypes.UINT]
        self.user32.UnregisterHotKey.argtypes = [self.wintypes.HWND, ctypes.c_int]
        self.user32.SetTimer.argtypes = [self.wintypes.HWND, self.wintypes.UINT,
                                         self.wintypes.UINT, ctypes.c_void_p]
        self.user32.KillTimer.argtypes = [self.wintypes.HWND, self.wintypes.UINT]
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(self.MSG), self.wintypes.HWND,
                                            self.wintypes.UINT, self.wintypes.UINT]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(self.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(self.MSG)]
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostMessageW.argtypes = [
            self.wintypes.HWND, self.wintypes.UINT, self.wintypes.WPARAM,
            self.wintypes.LPARAM,
        ]
        self.user32.DestroyWindow.argtypes = [self.wintypes.HWND]
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(self.POINT)]
        self.user32.CreatePopupMenu.restype = self.wintypes.HMENU
        self.user32.TrackPopupMenu.argtypes = [self.wintypes.HMENU, self.wintypes.UINT,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                self.wintypes.HWND, ctypes.c_void_p]
        self.user32.AppendMenuW.argtypes = [self.wintypes.HMENU, self.wintypes.UINT,
                                             ctypes.c_size_t, self.wintypes.LPCWSTR]
        self.user32.DestroyMenu.argtypes = [self.wintypes.HMENU]
        self.user32.SetForegroundWindow.argtypes = [self.wintypes.HWND]
        self.shell32.Shell_NotifyIconW.argtypes = [self.wintypes.DWORD,
                                                    ctypes.POINTER(self.NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = self.wintypes.BOOL
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, self.wintypes.BOOL,
                                                self.wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = self.wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [self.wintypes.HANDLE]
        self.kernel32.GetModuleHandleW.argtypes = [self.wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = self.wintypes.HMODULE
        self.user32.LoadIconW.argtypes = [self.wintypes.HINSTANCE, ctypes.c_void_p]
        self.user32.LoadIconW.restype = self.wintypes.HICON

    def acquire_single_instance(self) -> bool:
        ctypes.set_last_error(0)
        self.mutex = self.kernel32.CreateMutexW(None, False, "Local\\Jarvis.M07.Desktop")
        if not self.mutex:
            raise OSError(ctypes.get_last_error(), "could not create the Jarvis desktop instance guard")
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None
            return False
        return True

    def start(self, companion: DesktopCompanion, shortcut: Shortcut) -> bool:
        self.companion = companion
        self.shortcut = shortcut
        self._create_window()
        self._add_tray_icon()
        self.shortcut_available = bool(self.user32.RegisterHotKey(
            self.hwnd, self.HOTKEY_ID, shortcut.modifiers, shortcut.virtual_key,
        ))
        self.hotkey_registered = self.shortcut_available
        self.user32.SetTimer(self.hwnd, self.TIMER_ID, self.poll_interval_ms, None)
        return self.shortcut_available

    def _create_window(self) -> None:
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, self.wintypes.HWND, self.wintypes.UINT,
            self.wintypes.WPARAM, self.wintypes.LPARAM,
        )

        def window_proc(hwnd, message, wparam, lparam):
            try:
                if message == self.WM_TRAY:
                    event = lparam & 0xFFFF
                    if event in (self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                        self.companion.open_dashboard()
                    elif event in (self.WM_RBUTTONUP, self.WM_CONTEXTMENU):
                        self._show_menu()
                    return 0
                if message == self.WM_HOTKEY:
                    self.companion.open_dashboard()
                    return 0
                if message == self.WM_TIMER:
                    if self._tick is not None:
                        self._tick()
                    return 0
                if message == self.WM_DESTROY:
                    self.user32.PostQuitMessage(0)
                    return 0
            except Exception:
                LOGGER.warning("Desktop event failed")
                return 0
            return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._window_proc = WNDPROC(window_proc)
        instance = self.kernel32.GetModuleHandleW(None)
        window_class = self.WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(self.WNDCLASSEXW)
        window_class.lpfnWndProc = ctypes.cast(self._window_proc, ctypes.c_void_p).value
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        if not self.user32.RegisterClassExW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:
                raise OSError(error, "could not register the Jarvis tray window")
        self.hwnd = self.user32.CreateWindowExW(
            0, self._class_name, "Jarvis Desktop Companion", 0,
            0, 0, 0, 0, None, None, instance, None,
        )
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "could not create the Jarvis tray window")

    def _icon_data(self, flags: int) -> NOTIFYICONDATAW:
        data = self.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(self.NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = self.WM_TRAY
        data.hIcon = self.user32.LoadIconW(None, ctypes.c_void_p(32512))
        data.szTip = f"Jarvis — {self.backend_status}"[:127]
        return data

    def _add_tray_icon(self) -> None:
        data = self._icon_data(self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP)
        if not self.shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(data)):
            raise OSError(ctypes.get_last_error(), "could not add the Jarvis tray icon")
        self.icon_added = True

    def update_view(self, status: str, shortcut_available: bool,
                    notifications_enabled: bool) -> None:
        self.backend_status = status
        self.shortcut_available = shortcut_available
        self.notifications_enabled = notifications_enabled
        if not self.icon_added:
            return
        data = self._icon_data(self.NIF_TIP)
        self.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(data))

    def notify_backend_change(self, available: bool) -> None:
        if not self.icon_added:
            return
        data = self._icon_data(self.NIF_INFO)
        data.szInfoTitle = "Jarvis"
        data.szInfo = "Jarvis is available." if available else "Jarvis is unavailable."
        data.dwInfoFlags = self.NIIF_INFO
        self.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(data))

    def _show_menu(self) -> None:
        menu = self.user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user32.AppendMenuW(menu, self.MF_STRING, self.CMD_OPEN, "Open Jarvis dashboard")
            self.user32.AppendMenuW(menu, self.MF_STRING, self.CMD_VOICE, "Open voice controls")
            self.user32.AppendMenuW(menu, self.MF_STRING | self.MF_GRAYED, 0,
                                    f"{self.backend_status}")
            shortcut_text = self.shortcut.label
            if not self.shortcut_available:
                shortcut_text += " (unavailable — conflict or OS restriction)"
            self.user32.AppendMenuW(menu, self.MF_STRING | self.MF_GRAYED, 0,
                                    f"Shortcut: {shortcut_text}")
            self.user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            notify_label = "Notifications (on)" if self.notifications_enabled else "Notifications (off)"
            self.user32.AppendMenuW(menu, self.MF_STRING, self.CMD_NOTIFICATIONS, notify_label)
            self.user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            self.user32.AppendMenuW(menu, self.MF_STRING, self.CMD_EXIT, "Exit Jarvis companion")
            self.user32.SetForegroundWindow(self.hwnd)
            point = self.POINT()
            self.user32.GetCursorPos(ctypes.byref(point))
            command = self.user32.TrackPopupMenu(
                menu, self.TPM_RIGHTBUTTON | self.TPM_RETURNCMD,
                point.x, point.y, 0, self.hwnd, None,
            )
            if self.companion is not None:
                if command == self.CMD_OPEN:
                    self.companion.open_dashboard()
                elif command == self.CMD_VOICE:
                    self.companion.open_voice()
                elif command == self.CMD_NOTIFICATIONS:
                    self.companion.toggle_notifications()
                elif command == self.CMD_EXIT:
                    self.companion.request_stop()
            self.user32.PostMessageW(self.hwnd, 0, 0, 0)
        finally:
            self.user32.DestroyMenu(menu)

    def run_message_loop(self, tick: Callable[[], None]) -> None:
        self._tick = tick
        message = self.MSG()
        while True:
            result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                break
            self.user32.TranslateMessage(ctypes.byref(message))
            self.user32.DispatchMessageW(ctypes.byref(message))

    def request_exit(self) -> None:
        self.user32.PostQuitMessage(0)

    def close(self) -> None:
        if self.hwnd:
            self.user32.KillTimer(self.hwnd, self.TIMER_ID)
            if self.hotkey_registered:
                self.user32.UnregisterHotKey(self.hwnd, self.HOTKEY_ID)
                self.hotkey_registered = False
            if self.icon_added:
                data = self._icon_data(0)
                self.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(data))
                self.icon_added = False
            self.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.mutex:
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None
        self._window_proc = None
        self._tick = None


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the authenticated Jarvis loopback dashboard from the Windows tray."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="existing Jarvis dashboard port (default: 8765)")
    parser.add_argument("--shortcut", default=DEFAULT_SHORTCUT,
                        help="global shortcut, e.g. CTRL+ALT+J or WIN+SHIFT+J")
    parser.add_argument("--notifications", action="store_true",
                        help="opt in to generic backend connectivity notifications")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        url = dashboard_url(args.port)
        shortcut = parse_shortcut(args.shortcut)
        if os.name != "nt":
            parser.exit(0, "Jarvis desktop companion requires Windows; no process was started.\n")
        host = WindowsDesktopHost()
    except ValueError as exc:
        parser.error(str(exc))
    except OSError as exc:
        parser.exit(1, f"Jarvis desktop companion unavailable: {exc}\n")
    companion = DesktopCompanion(
        port=args.port, shortcut=shortcut,
        notifications_enabled=args.notifications, host=host,
    )
    try:
        result = companion.run()
    except OSError:
        LOGGER.warning("Jarvis desktop companion could not initialize")
        return 1
    if result == "already_running":
        _open_default_browser(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

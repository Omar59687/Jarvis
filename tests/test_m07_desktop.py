from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from urllib.error import HTTPError

from jarvis_core.model_provider import GenerationResult
from jarvis_core.voice import VoiceService
from prototypes.m03_b1.service import make_server

from prototypes.m07_desktop.desktop import (
    DEFAULT_SHORTCUT,
    DesktopCompanion,
    backend_is_available,
    create_parser,
    dashboard_url,
    main,
    parse_shortcut,
    WindowsDesktopHost,
)


class FakeDesktopHost:
    def __init__(self, *, instance_available=True, shortcut_available=True):
        self.instance_available = instance_available
        self.shortcut_available = shortcut_available
        self.companion = None
        self.started = False
        self.closed = False
        self.exit_requested = False
        self.views = []
        self.notifications = []

    def acquire_single_instance(self):
        return self.instance_available

    def start(self, companion, shortcut):
        self.companion = companion
        self.started = True
        return self.shortcut_available

    def update_view(self, status, shortcut_available, notifications_enabled):
        self.views.append((status, shortcut_available, notifications_enabled))

    def notify_backend_change(self, available):
        self.notifications.append(available)

    def run_message_loop(self, tick):
        tick()
        self.companion.request_stop()

    def request_exit(self):
        self.exit_requested = True

    def close(self):
        self.closed = True


class DesktopCompanionTests(unittest.TestCase):
    def test_shortcut_parser_requires_modifier_and_supported_key(self):
        shortcut = parse_shortcut("ctrl+alt+j")
        self.assertEqual(shortcut.label, "CTRL+ALT+J")
        self.assertEqual(shortcut.modifiers, 0x4003)
        self.assertEqual(shortcut.virtual_key, ord("J"))
        self.assertEqual(parse_shortcut("WIN+SHIFT+F12").virtual_key, 0x7B)
        for invalid in ("J", "CTRL+J+K", "CTRL+CTRL+J", "ALT+DELETE", "", "CTRL+F25"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_shortcut(invalid)

    def test_dashboard_urls_are_fixed_to_numeric_loopback(self):
        self.assertEqual(dashboard_url(8765), "http://127.0.0.1:8765/")
        self.assertEqual(dashboard_url(8765, voice=True),
                         "http://127.0.0.1:8765/#voice-panel")
        for port in (0, 65536, True, "8765"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                dashboard_url(port)

    def test_browser_open_reuses_the_dashboard_origin_without_auth_material(self):
        host = FakeDesktopHost()
        opened = []
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
            open_url=opened.append, health_check=lambda _: False,
        )
        companion.open_dashboard()
        companion.open_voice()
        self.assertEqual(opened, [dashboard_url(8765), dashboard_url(8765, voice=True)])
        self.assertTrue(all("@" not in url and "token" not in url for url in opened))

    def test_run_owns_only_desktop_lifecycle_and_closes_gracefully(self):
        host = FakeDesktopHost()
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
            health_check=lambda _: True,
        )
        self.assertEqual(companion.run(), "stopped")
        self.assertTrue(host.started)
        self.assertTrue(host.exit_requested)
        self.assertTrue(host.closed)
        self.assertIn(("Backend connected", True, False), host.views)

    def test_single_instance_refusal_does_not_initialize_or_close_another_instance(self):
        host = FakeDesktopHost(instance_available=False)
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
        )
        self.assertEqual(companion.run(), "already_running")
        self.assertFalse(host.started)
        self.assertFalse(host.closed)

    def test_shortcut_conflict_keeps_tray_available_and_exits_cleanly(self):
        host = FakeDesktopHost(shortcut_available=False)
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
            health_check=lambda _: False,
        )
        self.assertEqual(companion.run(), "stopped")
        self.assertTrue(host.started)
        self.assertIn(("Backend unavailable", False, False), host.views)
        self.assertTrue(host.closed)

    def test_notifications_require_opt_in_and_include_only_generic_connectivity(self):
        host = FakeDesktopHost()
        states = iter((False, True))
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
            health_check=lambda _: next(states),
        )
        companion.refresh_backend_status()
        companion.refresh_backend_status()
        self.assertEqual(host.notifications, [])

        host = FakeDesktopHost()
        states = iter((False, True))
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
            health_check=lambda _: next(states),
        )
        companion.refresh_backend_status()
        companion.toggle_notifications()
        companion.refresh_backend_status()
        self.assertEqual(host.notifications, [True])

    def test_notification_toggle_is_an_explicit_session_opt_in(self):
        host = FakeDesktopHost()
        companion = DesktopCompanion(
            port=8765, shortcut=parse_shortcut(DEFAULT_SHORTCUT),
            notifications_enabled=False, host=host,
        )
        companion.toggle_notifications()
        self.assertTrue(companion.notifications_enabled)
        self.assertEqual(host.views[-1][2], True)

    def test_health_probe_accepts_only_expected_bounded_local_response(self):
        response = io.BytesIO(json.dumps({"status": "ok", "service": "m03-b2"}).encode())
        response.status = 200

        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return response

        opener = FakeOpener()
        with patch("prototypes.m07_desktop.desktop.urllib.request.build_opener",
                   return_value=opener):
            self.assertTrue(backend_is_available(8765))
        self.assertEqual(opener.request.full_url, "http://127.0.0.1:8765/health")
        self.assertEqual(opener.timeout, 0.75)

    def test_health_probe_rejects_redirects_and_unexpected_services(self):
        class RedirectingOpener:
            def open(self, request, timeout):
                error = HTTPError(request.full_url, 302, "redirect", {}, None)
                error.close()
                raise error

        with patch("prototypes.m07_desktop.desktop.urllib.request.build_opener",
                   return_value=RedirectingOpener()):
            self.assertFalse(backend_is_available(8765))

        response = io.BytesIO(b'{"status":"ok","service":"other"}')
        response.status = 200

        class UnexpectedServiceOpener:
            def open(self, request, timeout):
                return response

        with patch("prototypes.m07_desktop.desktop.urllib.request.build_opener",
                   return_value=UnexpectedServiceOpener()):
            self.assertFalse(backend_is_available(8765))

    def test_notifications_default_off_and_opt_in_flag_is_explicit(self):
        self.assertFalse(create_parser().parse_args([]).notifications)
        self.assertTrue(create_parser().parse_args(["--notifications"]).notifications)

    def test_non_windows_exits_without_starting_desktop_or_backend(self):
        with patch("prototypes.m07_desktop.desktop.os.name", "posix"):
            with self.assertRaises(SystemExit) as raised:
                main([])
        self.assertEqual(raised.exception.code, 0)

    @unittest.skipUnless(os.name == "nt", "Windows native API loading check")
    def test_windows_host_loads_native_api_signatures_without_starting(self):
        host = WindowsDesktopHost()
        self.assertFalse(host.icon_added)
        self.assertFalse(host.hotkey_registered)
        self.assertIsNone(host.hwnd)
        self.assertIsNone(host.mutex)



class _StubGenerator:
    def generate(self, prompt, model, *, system_prompt=None,
                 temperature=0.3, max_output_tokens=192):
        return GenerationResult(model=model, text="ok", done=True)


def _start_isolated_backend():
    server = make_server(
        0,
        provider="test",
        model="m07-isolated-test",
        generator=_StubGenerator(),
        pairing_code="m07-isolated-pair-secret",
        voice_service=VoiceService(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_isolated_backend(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


class DesktopBackendIntegrationTests(unittest.TestCase):
    def test_m07_compatible_backend_serves_health_and_voice_panel_and_survives_companion_exit(self):
        server, thread = _start_isolated_backend()
        port = server.server_address[1]
        try:
            self.assertTrue(backend_is_available(port))

            with urllib.request.urlopen(dashboard_url(port), timeout=2) as response:
                html = response.read().decode("utf-8")
            self.assertIn('id="voice-panel"', html)
            self.assertIn('id="voice-ptt"', html)
            self.assertIn('id="voice-state"', html)

            with urllib.request.urlopen(dashboard_url(port) + "voice-ui-state.mjs", timeout=2) as response:
                self.assertEqual(response.status, 200)

            host = FakeDesktopHost()
            opened = []
            companion = DesktopCompanion(
                port=port,
                shortcut=parse_shortcut(DEFAULT_SHORTCUT),
                notifications_enabled=False,
                host=host,
                open_url=opened.append,
            )
            companion.open_voice()
            self.assertEqual(opened, [f"http://127.0.0.1:{port}/#voice-panel"])
            self.assertEqual(companion.run(), "stopped")
            self.assertIn(("Backend connected", True, False), host.views)
            self.assertTrue(host.closed)
            # Companion shutdown must never stop the running backend.
            self.assertTrue(backend_is_available(port))
        finally:
            _stop_isolated_backend(server, thread)

        self.assertFalse(backend_is_available(port))


@unittest.skipUnless(os.name == "nt", "Windows native Win32 lifecycle tests")
class WindowsNativeLifecycleTests(unittest.TestCase):
    """Isolated Win32 lifecycle checks exercising real mutex, window, hotkey, and timer APIs.

    Shell_NotifyIconW is intercepted so tests inspect exact NOTIFYICONDATAW payloads
    and run reliably on headless Windows runners without touching a user's taskbar.
    """

    @staticmethod
    def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    def _pick_free_shortcut(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for key_name in ("CTRL+ALT+F22", "CTRL+ALT+F23", "CTRL+ALT+F24"):
            candidate = parse_shortcut(key_name)
            if user32.RegisterHotKey(None, 0x4D07, candidate.modifiers, candidate.virtual_key):
                user32.UnregisterHotKey(None, 0x4D07)
                return candidate
        self.skipTest("No free test function-key shortcut available in this Windows session")

    def _skip_if_companion_mutex_already_held(self):
        probe = WindowsDesktopHost(poll_interval_ms=1000)
        if not probe.acquire_single_instance():
            self.skipTest("Another Jarvis M07 desktop companion instance is already running")
        probe.close()

    @staticmethod
    def _intercept_tray_calls(host: WindowsDesktopHost):
        events: list[tuple[int, int, str, str, str, int]] = []

        def fake_shell_notify_icon(message, data_ptr):
            data = ctypes.cast(data_ptr, ctypes.POINTER(host.NOTIFYICONDATAW)).contents
            events.append((
                int(message),
                int(data.uFlags),
                str(data.szTip),
                str(data.szInfoTitle),
                str(data.szInfo),
                int(data.dwInfoFlags),
            ))
            return 1

        host.shell32.Shell_NotifyIconW = fake_shell_notify_icon
        return events

    def test_windows_single_instance_mutex_blocks_in_process_and_child_process(self):
        self._skip_if_companion_mutex_already_held()
        shortcut = self._pick_free_shortcut()
        host1 = WindowsDesktopHost(poll_interval_ms=1000)
        self._intercept_tray_calls(host1)
        self.assertTrue(host1.acquire_single_instance())
        try:
            # In-process second instance is refused cleanly without starting or closing.
            host2 = WindowsDesktopHost(poll_interval_ms=1000)
            comp2 = DesktopCompanion(
                port=8765, shortcut=shortcut, notifications_enabled=False, host=host2,
            )
            self.assertEqual(comp2.run(), "already_running")
            self.assertIsNone(host2.hwnd)
            self.assertFalse(host2.icon_added)
            self.assertIsNone(host2.mutex)

            # Separate OS child process detects ERROR_ALREADY_EXISTS, opens dashboard, exits 0.
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            child_script = (
                "from unittest.mock import patch\n"
                "import prototypes.m07_desktop.desktop as d\n"
                "opened = []\n"
                "with patch.object(d, '_open_default_browser', side_effect=opened.append):\n"
                f"    rc = d.main(['--port', '8765', '--shortcut', {shortcut.label!r}])\n"
                "assert rc == 0, rc\n"
                "assert opened == ['http://127.0.0.1:8765/'], opened\n"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            proc = subprocess.run(
                [sys.executable, "-c", child_script],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
        finally:
            host1.close()

        # Once instance 1 exits and closes its mutex, a fresh instance acquires it.
        host3 = WindowsDesktopHost(poll_interval_ms=1000)
        try:
            self.assertTrue(host3.acquire_single_instance())
        finally:
            host3.close()

    def test_windows_clean_shutdown_releases_window_tray_hotkey_mutex_and_preserves_backend(self):
        self._skip_if_companion_mutex_already_held()
        shortcut = self._pick_free_shortcut()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        server, srv_thread = _start_isolated_backend()
        port = server.server_address[1]
        try:
            host = WindowsDesktopHost(poll_interval_ms=1000)
            tray_events = self._intercept_tray_calls(host)
            stop_now = threading.Event()
            ready = threading.Event()
            result_box: dict[str, object] = {}

            def health_and_stop(target_port: int) -> bool:
                ok = backend_is_available(target_port)
                if ok:
                    ready.set()
                if stop_now.is_set():
                    # Runs on the UI message-loop thread, matching tray CMD_EXIT -> request_stop().
                    companion.request_stop()
                return ok

            companion = DesktopCompanion(
                port=port,
                shortcut=shortcut,
                notifications_enabled=False,
                host=host,
                health_check=health_and_stop,
            )

            def run_ui():
                result_box["result"] = companion.run()

            ui_thread = threading.Thread(target=run_ui, daemon=True)
            ui_thread.start()
            try:
                self.assertTrue(ready.wait(timeout=5.0), "Companion did not complete initial health check")
                self.assertTrue(self._wait_until(lambda: host._tick is not None))
                self.assertIsNotNone(host.hwnd)
                self.assertTrue(host.icon_added)
                self.assertTrue(host.hotkey_registered)
                self.assertIsNotNone(host.mutex)
                self.assertEqual(host.backend_status, "Backend connected")

                # Confirm the hotkey is genuinely registered at the OS level.
                ctypes.set_last_error(0)
                dup = user32.RegisterHotKey(None, 0x4D08, shortcut.modifiers, shortcut.virtual_key)
                self.assertFalse(dup)
                self.assertEqual(ctypes.get_last_error(), host.ERROR_HOTKEY_ALREADY_REGISTERED)

                # Trigger WM_TIMER on the UI window so companion.request_stop() runs on the UI thread.
                stop_now.set()
                user32.PostMessageW(host.hwnd, host.WM_TIMER, host.TIMER_ID, 0)
                ui_thread.join(timeout=5.0)
                self.assertFalse(ui_thread.is_alive(), "UI message loop did not exit cleanly")
            finally:
                if ui_thread.is_alive() and host.hwnd:
                    user32.PostMessageW(host.hwnd, 0x0010, 0, 0)
                    ui_thread.join(timeout=2.0)

            self.assertEqual(result_box.get("result"), "stopped")
            self.assertIsNone(host.hwnd)
            self.assertFalse(host.icon_added)
            self.assertFalse(host.hotkey_registered)
            self.assertIsNone(host.mutex)
            self.assertTrue(any(msg == host.NIM_ADD for msg, *_ in tray_events))
            self.assertTrue(any(msg == host.NIM_DELETE for msg, *_ in tray_events))

            # Verify OS hotkey was actually unregistered.
            re_ok = user32.RegisterHotKey(None, 0x4D09, shortcut.modifiers, shortcut.virtual_key)
            self.assertTrue(re_ok, "Hotkey remained registered after clean shutdown")
            user32.UnregisterHotKey(None, 0x4D09)

            # Verify OS mutex was actually released.
            probe = WindowsDesktopHost(poll_interval_ms=1000)
            try:
                self.assertTrue(probe.acquire_single_instance())
            finally:
                probe.close()

            # Verify isolated backend is untouched and still healthy.
            self.assertTrue(backend_is_available(port))
        finally:
            _stop_isolated_backend(server, srv_thread)

    def test_windows_global_shortcut_conflict_falls_back_cleanly_without_disturbing_owner(self):
        self._skip_if_companion_mutex_already_held()
        shortcut = self._pick_free_shortcut()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.assertTrue(user32.RegisterHotKey(None, 0x4D0A, shortcut.modifiers, shortcut.virtual_key))
        try:
            host = WindowsDesktopHost(poll_interval_ms=1000)
            tray_events = self._intercept_tray_calls(host)
            stop_now = threading.Event()
            ready = threading.Event()
            result_box: dict[str, object] = {}

            def health_and_stop(_port: int) -> bool:
                ready.set()
                if stop_now.is_set():
                    companion.request_stop()
                return True

            companion = DesktopCompanion(
                port=8765,
                shortcut=shortcut,
                notifications_enabled=False,
                host=host,
                health_check=health_and_stop,
            )
            ui_thread = threading.Thread(
                target=lambda: result_box.setdefault("result", companion.run()),
                daemon=True,
            )
            ui_thread.start()
            try:
                self.assertTrue(ready.wait(timeout=5.0))
                self.assertTrue(self._wait_until(lambda: host._tick is not None))
                self.assertTrue(host.icon_added)
                self.assertFalse(host.shortcut_available)
                self.assertFalse(host.hotkey_registered)
                stop_now.set()
                user32.PostMessageW(host.hwnd, host.WM_TIMER, host.TIMER_ID, 0)
                ui_thread.join(timeout=5.0)
                self.assertFalse(ui_thread.is_alive())
            finally:
                if ui_thread.is_alive() and host.hwnd:
                    user32.PostMessageW(host.hwnd, 0x0010, 0, 0)
                    ui_thread.join(timeout=2.0)

            self.assertEqual(result_box.get("result"), "stopped")
            self.assertTrue(any(msg == host.NIM_DELETE for msg, *_ in tray_events))
            self.assertIsNone(host.hwnd)
            self.assertIsNone(host.mutex)

            # External owner's hotkey registration must still be intact.
            ctypes.set_last_error(0)
            still_taken = user32.RegisterHotKey(None, 0x4D0B, shortcut.modifiers, shortcut.virtual_key)
            self.assertFalse(still_taken)
            self.assertEqual(ctypes.get_last_error(), host.ERROR_HOTKEY_ALREADY_REGISTERED)
        finally:
            user32.UnregisterHotKey(None, 0x4D0A)

    def test_windows_opt_in_notifications_emit_generic_win32_balloon_only_when_enabled(self):
        self._skip_if_companion_mutex_already_held()
        shortcut = self._pick_free_shortcut()
        host = WindowsDesktopHost(poll_interval_ms=60_000)
        tray_events = self._intercept_tray_calls(host)
        states = iter((False, True, False, True))
        companion = DesktopCompanion(
            port=8765,
            shortcut=shortcut,
            notifications_enabled=False,
            host=host,
            health_check=lambda _: next(states),
        )
        self.assertTrue(host.acquire_single_instance())
        try:
            companion._instance_acquired = True
            companion._shortcut_available = host.start(companion, shortcut)

            # Notifications default off: False -> True transition emits no balloon.
            companion.refresh_backend_status()
            companion.refresh_backend_status()
            balloons = [e for e in tray_events if e[0] == host.NIM_MODIFY and (e[1] & host.NIF_INFO)]
            self.assertEqual(balloons, [])

            # Explicit opt-in toggle enables generic transition balloons only.
            companion.toggle_notifications()
            companion.refresh_backend_status()  # True -> False
            companion.refresh_backend_status()  # False -> True
            balloons = [
                (title, info, info_flags)
                for msg, flags, _tip, title, info, info_flags in tray_events
                if msg == host.NIM_MODIFY and (flags & host.NIF_INFO)
            ]
            self.assertEqual(balloons, [
                ("Jarvis", "Jarvis is unavailable.", host.NIIF_INFO),
                ("Jarvis", "Jarvis is available.", host.NIIF_INFO),
            ])
        finally:
            companion.close()

if __name__ == "__main__":
    unittest.main()

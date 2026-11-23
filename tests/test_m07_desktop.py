from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

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


if __name__ == "__main__":
    unittest.main()

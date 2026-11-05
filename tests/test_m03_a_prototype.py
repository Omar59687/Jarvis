from __future__ import annotations

import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from prototypes.m03_a.service import make_server


class M03APrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = make_server(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://{self.server.expected_host}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_binds_only_loopback_and_health_is_harmless(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        with urlopen(self.base_url + "/health", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
            headers = response.headers
        self.assertEqual(body, {"status": "ok", "prototype": "m03-a", "data_connected": False})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_mock_ui_is_synthetic_bilingual_galaxy_with_unavailable_chat(self) -> None:
        with urlopen(self.base_url + "/", timeout=2) as response:
            page = response.read().decode("utf-8")
            headers = response.headers
        for expected in ("workspace", "search", "chat", "Knowledge galaxy", "synthetic",
                         "data-ar=", "no question will be sent", "NO BACKEND"):
            self.assertIn(expected.lower(), page.lower())
        self.assertIn('disabled', page.lower())
        self.assertNotIn("<form", page.lower())
        with urlopen(self.base_url + "/app.js", timeout=2) as response:
            script = response.read().decode("utf-8")
        self.assertIn('document.documentElement.dir = language === "ar" ? "rtl" : "ltr"', script)
        self.assertIn('import("/vendor/three.module.js")', script)
        self.assertNotIn("https://", script)
        self.assertNotIn("fetch(", script.replace('fetch("/health"', ""))
        self.assertEqual(headers.get("Content-Security-Policy"),
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

    def test_galaxy_assets_are_local_and_allowlisted(self) -> None:
        for path, marker in (("/galaxy-model.mjs", "connectedNodeIds"),
                             ("/vendor/three.module.js", "three.js"),
                             ("/vendor/three.core.js", "REVISION = '186'")):
            with self.subTest(path=path), urlopen(self.base_url + path, timeout=5) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertIn(marker.encode(), body)
                self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        license_text = (Path(__file__).parents[1] / "prototypes/m03_a/vendor/THREE-LICENSE.txt").read_text()
        self.assertIn("MIT License", license_text)

    def test_host_header_rejects_unexpected_origin_host(self) -> None:
        request = Request(self.base_url + "/health", headers={"Host": "attacker.example"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 421)
        self.assertEqual(json.loads(caught.exception.read().decode("utf-8")),
                         {"error": "host_not_allowed"})

    def test_no_private_or_mutating_api_and_no_cors_preflight(self) -> None:
        for path in ("/api/session", "/api/workspaces", "/api/search", "/api/chat"):
            request = Request(self.base_url + path, data=b"{}", method="POST",
                              headers={"Origin": "https://attacker.example"})
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 405)
            self.assertIsNone(caught.exception.headers.get("Access-Control-Allow-Origin"))

        request = Request(self.base_url + "/api/search", method="OPTIONS",
                          headers={"Origin": "https://attacker.example",
                                   "Access-Control-Request-Method": "POST"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 405)
        self.assertIsNone(caught.exception.headers.get("Access-Control-Allow-Origin"))

    def test_unknown_path_cannot_read_outside_the_static_allowlist(self) -> None:
        request = Request(self.base_url + "/../AGENTS.md")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

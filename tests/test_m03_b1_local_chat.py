from __future__ import annotations

import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core.ollama import GenerationResult, OllamaClient, OllamaModelUnavailable
from prototypes.m03_b1.service import (
    API_PREFIX,
    GENERAL_CHAT_SYSTEM_PROMPT,
    MODEL,
    SESSION_IDLE_SECONDS,
    make_server,
)


class FakeGenerator:
    def __init__(self, result=None, error=None):
        self.result = result or GenerationResult(model=MODEL, text="I'm doing well, thanks!", done=True)
        self.error = error
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def generate(self, prompt, model, *, system_prompt=None, settings=None):
        self.calls.append((prompt, model, system_prompt, settings))
        self.entered.set()
        if self.block:
            self.release.wait(timeout=4)
        if self.error is not None:
            raise self.error
        return self.result


class MutableClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class M03B1LocalChatTests(unittest.TestCase):
    def setUp(self):
        self.generator = FakeGenerator()
        self.server = make_server(0, generator=self.generator, pairing_code="test-pairing-code")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://{self.server.expected_host}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, *, payload=None, raw=None, method="POST", headers=None, origin="expected", host="expected"):
        request_headers = {"Content-Type": "application/json"}
        if origin == "expected":
            request_headers["Origin"] = self.server.expected_origin
        elif origin is not None:
            request_headers["Origin"] = origin
        if host == "expected":
            request_headers["Host"] = self.server.expected_host
        elif host is not None:
            request_headers["Host"] = host
        if headers:
            request_headers.update(headers)
        body = raw
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(self.base + path, data=body, method=method, headers=request_headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def pair(self):
        status, headers, body = self.request(f"{API_PREFIX}/session/pair", payload={
            "pairing_code": self.server.pairing.terminal_code
        })
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        csrf = json.loads(body)["csrf_token"]
        return cookie, csrf

    def chat(self, *, cookie=None, csrf=None, message="How are you?", origin="expected", host="expected", raw=None):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if csrf is not None:
            headers["X-Jarvis-CSRF"] = csrf
        body = raw if raw is not None else json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
        return self.request(f"{API_PREFIX}/chat", raw=body, headers=headers, origin=origin, host=host)

    def test_binds_loopback_serves_shared_galaxy_and_harmless_health(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("/health", method="GET", origin=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "service": "m03-b1", "data_connected": False})
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        status, _, body = self.request("/", method="GET", origin=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Knowledge galaxy", body)
        status, _, body = self.request("/chat.css", method="GET", origin=None)
        self.assertEqual(status, 200)
        self.assertIn(b".chat-form", body)

    def test_pairing_sets_http_only_strict_cookie_and_one_time_code_cannot_replay(self):
        code = self.server.pairing.terminal_code
        status, headers, body = self.request(f"{API_PREFIX}/session/pair", payload={"pairing_code": code})
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn(f"Path={API_PREFIX}", cookie)
        self.assertIn("Max-Age=14400", cookie)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        payload = json.loads(body)
        self.assertEqual(payload["model"], MODEL)
        self.assertNotEqual(payload["csrf_token"], code)
        status, _, _ = self.request(f"{API_PREFIX}/session/pair", payload={"pairing_code": code})
        self.assertEqual(status, 401)
        self.assertEqual(self.generator.calls, [])

    def test_pairing_retries_and_expiry_are_bounded(self):
        for _ in range(5):
            status, _, _ = self.request(f"{API_PREFIX}/session/pair", payload={"pairing_code": "wrong"})
            self.assertEqual(status, 401)
        status, _, _ = self.request(f"{API_PREFIX}/session/pair", payload={
            "pairing_code": self.server.pairing.terminal_code
        })
        self.assertEqual(status, 401)
        clock = MutableClock(0)
        other = make_server(0, generator=FakeGenerator(), pairing_code="expires", clock=clock)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            clock.value = 301
            request = Request(f"http://{other.expected_host}{API_PREFIX}/session/pair",
                              data=json.dumps({"pairing_code": "expires"}).encode(), method="POST",
                              headers={"Host": other.expected_host, "Origin": other.expected_origin,
                                       "Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 401)
        finally:
            other.shutdown()
            other.server_close()
            thread.join(timeout=2)

    def test_unauthenticated_and_expired_sessions_never_call_generator(self):
        status, _, _ = self.chat()
        self.assertEqual(status, 401)
        cookie, csrf = self.pair()
        clock = MutableClock(10)
        other_generator = FakeGenerator()
        other = make_server(0, generator=other_generator, pairing_code="expiry-code", clock=clock)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            origin = other.expected_origin
            host = other.expected_host
            status, headers, body = self.request_to(other, f"{API_PREFIX}/session/pair", {"pairing_code": "expiry-code"})
            self.assertEqual(status, 200)
            expired_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
            expired_csrf = json.loads(body)["csrf_token"]
            clock.value += SESSION_IDLE_SECONDS + 1
            status, _, _ = self.request_to(other, f"{API_PREFIX}/chat", {"message": "hello"},
                                           cookie=expired_cookie, csrf=expired_csrf)
            self.assertEqual(status, 401)
            self.assertEqual(other_generator.calls, [])
        finally:
            other.shutdown()
            other.server_close()
            thread.join(timeout=2)
        self.assertEqual(self.generator.calls, [])

    def request_to(self, server, path, payload, *, cookie=None, csrf=None):
        headers = {"Host": server.expected_host, "Origin": server.expected_origin,
                   "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-Jarvis-CSRF"] = csrf
        req = Request(f"http://{server.expected_host}{path}", data=json.dumps(payload).encode(),
                      method="POST", headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def test_origin_host_and_csrf_are_checked_before_generation(self):
        cookie, csrf = self.pair()
        for origin in ("https://attacker.example", "null", None):
            status, headers, _ = self.chat(cookie=cookie, csrf=csrf, origin=origin)
            self.assertEqual(status, 403)
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        status, _, _ = self.chat(cookie=cookie, csrf=csrf, host="attacker.example")
        self.assertEqual(status, 421)
        status, _, _ = self.chat(cookie=cookie, csrf="wrong")
        self.assertEqual(status, 403)
        self.assertEqual(self.generator.calls, [])

    def test_authorized_chat_uses_fixed_model_general_prompt_and_no_history(self):
        cookie, csrf = self.pair()
        for message in ("How are you?", "Please say hello in Arabic."):
            status, headers, body = self.chat(cookie=cookie, csrf=csrf, message=message)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"reply": "I'm doing well, thanks!", "model": MODEL})
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        self.assertEqual(len(self.generator.calls), 2)
        prompt, model, system_prompt, settings = self.generator.calls[0]
        self.assertEqual(prompt, "How are you?")
        self.assertEqual(model, "gemma3:4b")
        self.assertEqual(system_prompt, GENERAL_CHAT_SYSTEM_PROMPT)
        self.assertIn("no access to the user's memories", system_prompt)
        self.assertNotIn("[D1]", system_prompt)
        self.assertEqual(settings.num_predict, 192)

    def test_duplicate_generation_is_rejected_and_chat_rate_is_bounded(self):
        cookie, csrf = self.pair()
        self.generator.block = True
        first_result = []
        first = threading.Thread(target=lambda: first_result.append(
            self.chat(cookie=cookie, csrf=csrf, message="first")
        ), daemon=True)
        first.start()
        self.assertTrue(self.generator.entered.wait(timeout=2))
        status, _, _ = self.chat(cookie=cookie, csrf=csrf, message="duplicate")
        self.assertEqual(status, 409)
        self.assertEqual(len(self.generator.calls), 1)
        self.generator.release.set()
        first.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_result[0][0], 200)

        self.generator.block = False
        for _ in range(5):
            status, _, _ = self.chat(cookie=cookie, csrf=csrf)
            self.assertEqual(status, 200)
        status, _, body = self.chat(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body), {"error": "rate_limited"})

    def test_request_limits_and_malformed_messages_are_rejected(self):
        cookie, csrf = self.pair()
        too_large = b"{" + b" " * 4096 + b"}"
        status, _, _ = self.chat(cookie=cookie, csrf=csrf, raw=too_large)
        self.assertEqual(status, 413)
        status, _, _ = self.chat(cookie=cookie, csrf=csrf, message="x" * 1001)
        self.assertEqual(status, 400)
        status, _, _ = self.chat(cookie=cookie, csrf=csrf, message="  ")
        self.assertEqual(status, 400)
        self.assertEqual(self.generator.calls, [])

    def test_model_errors_are_redacted_and_local_model_absence_is_clear(self):
        cookie, csrf = self.pair()
        self.generator.error = RuntimeError("secret-token C:\\private\\path stack trace")
        status, _, body = self.chat(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "generation_failed"})
        self.assertNotIn(b"secret-token", body)
        self.assertNotIn(b"private", body)
        self.generator.error = OllamaModelUnavailable("private local detail")
        status, _, body = self.chat(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "model_not_installed"})
        self.generator.error = None
        self.generator.result = GenerationResult(model=MODEL, text="x" * 16001, done=True)
        status, _, body = self.chat(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "generation_failed"})
        self.generator.result = GenerationResult(model=MODEL, text="\ud800", done=True)
        status, _, body = self.chat(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "generation_failed"})

    def test_production_factory_explicitly_allows_only_local_gemma_without_network_call(self):
        server = make_server(0)
        try:
            self.assertIsInstance(server.generator, OllamaClient)
            self.assertEqual(server.generator.allowed_models, frozenset({"gemma3:4b"}))
            self.assertEqual(server.generator._origin, "http://127.0.0.1:11434")
            self.assertEqual(server.generator.timeout, 45.0)
        finally:
            server.server_close()

    def test_frontend_uses_same_origin_chat_and_never_persists_or_calls_ollama(self):
        app_js = (Path(__file__).parents[1] / "prototypes/m03_b1/app.js").read_text()
        page = (Path(__file__).parents[1] / "prototypes/m03_b1/index.html").read_text()
        self.assertIn('fetch("/api/v1/chat"', app_js)
        self.assertIn('"X-Jarvis-CSRF": csrfToken', app_js)
        self.assertIn("sending || !csrfToken", app_js)
        self.assertIn("autocomplete=\"one-time-code\"", page)
        self.assertNotIn("localStorage", app_js)
        self.assertNotIn("sessionStorage", app_js)
        self.assertNotIn("11434", app_js)
        self.assertNotIn('fetch("http', app_js.lower())
        self.assertNotIn('"/api/chat"', app_js.lower())
        self.assertNotIn("/api/v1/chat", (Path(__file__).parents[1] / "prototypes/m03_a/app.js").read_text())


if __name__ == "__main__":
    unittest.main()

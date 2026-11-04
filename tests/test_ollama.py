import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from jarvis_core.ollama import (
    GenerationSettings,
    HTTPResponse,
    OllamaClient,
    OllamaConfigurationError,
    OllamaHTTPError,
    OllamaModelUnavailable,
    OllamaProtocolError,
    OllamaResponseTooLarge,
    OllamaTimeout,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, body, timeout, max_response_bytes):
        self.requests.append((method, url, body, timeout, max_response_bytes))
        response = self.responses.pop(0)
        if len(response.body) > max_response_bytes:
            raise OllamaResponseTooLarge("oversized")
        return response


def json_response(value, status=200, headers=None):
    return HTTPResponse(status, headers or {}, json.dumps(value, ensure_ascii=False).encode("utf-8"))


def client_with(*responses, timeout=12):
    transport = FakeTransport(responses)
    return OllamaClient("http://127.0.0.1:11434", ["qwen2.5:3b"], transport=transport, timeout=timeout), transport


class OllamaAdapterTests(unittest.TestCase):
    def test_local_generation_is_deterministic_configurable_and_unicode_safe(self):
        client, transport = client_with(
            json_response({"models": [{"name": "qwen2.5:3b"}]}),
            json_response({"message": {"role": "assistant", "content": "مرحبًا — hello"}, "done": True}),
        )
        settings = GenerationSettings(temperature=0, seed=7, num_ctx=4096, num_predict=256)
        result = client.generate("اشرح this", "qwen2.5:3b", system_prompt="Respond bilingually", settings=settings)
        self.assertEqual(result.text, "مرحبًا — hello")
        self.assertTrue(result.done)
        req = json.loads(transport.requests[1][2].decode("utf-8"))
        self.assertEqual(req["options"], {"temperature": 0, "seed": 7, "num_ctx": 4096, "num_predict": 256})
        self.assertFalse(req["stream"])
        self.assertEqual(transport.requests[0][0:2], ("GET", "http://127.0.0.1:11434/api/tags"))

    def test_unallowlisted_or_not_installed_model_never_generates(self):
        client, transport = client_with(json_response({"models": []}))
        with self.assertRaises(OllamaConfigurationError):
            client.generate("hello", "another:1b")
        with self.assertRaises(OllamaModelUnavailable):
            client.generate("hello", "qwen2.5:3b")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.requests[0][0], "GET")

    def test_no_model_download_endpoint_or_implicit_model_selection(self):
        with self.assertRaises(OllamaConfigurationError):
            OllamaClient("http://127.0.0.1:11434", [])
        with self.assertRaises(OllamaConfigurationError):
            OllamaClient("http://127.0.0.1:11434", ["qwen2.5:3b:cloud"])
        client, transport = client_with(json_response({"models": [{"name": "qwen2.5:3b"}]}))
        with self.assertRaises(OllamaConfigurationError):
            client.generate("hello", "qwen2.5:3b", settings=GenerationSettings(num_predict=513))
        self.assertEqual(transport.requests, [])

    def test_remote_and_unsupported_urls_rejected(self):
        for url in (
            "https://127.0.0.1:11434", "http://8.8.8.8:11434", "http://localhost:11434",
            "http://127.0.0.1", "http://user@127.0.0.1:11434", "http://127.0.0.1:11434/api",
            "http://127.0.0.1:11434/?x=1", "http://127.0.0.1.evil:11434",
        ):
            with self.subTest(url=url), self.assertRaises(OllamaConfigurationError):
                OllamaClient(url, ["qwen2.5:3b"])
        self.assertEqual(OllamaClient("http://[::1]:11434", ["qwen2.5:3b"]).allowed_models,
                         frozenset({"qwen2.5:3b"}))

    def test_redirect_and_bad_responses_fail_closed(self):
        client, _ = client_with(HTTPResponse(302, {"Location": "http://example.com/"}, b""))
        with self.assertRaises(OllamaHTTPError):
            client.installed_models()
        for response in (HTTPResponse(200, {}, b"not json"), json_response({"other": []}),
                         json_response({"models": ["bad entry"]})):
            client, _ = client_with(response)
            with self.assertRaises(OllamaProtocolError):
                client.installed_models()
        client, _ = client_with(json_response({"models": [{"name": "qwen2.5:3b"}]}),
                                json_response({"message": {"content": "ok", "tool_calls": []}, "done": True}))
        with self.assertRaises(OllamaProtocolError):
            client.generate("hello", "qwen2.5:3b")

    def test_timeout_and_oversized_responses_are_reported(self):
        class TimeoutTransport:
            def request(self, *args):
                raise socket.timeout()

        client = OllamaClient("http://127.0.0.1:11434", ["qwen2.5:3b"], transport=TimeoutTransport())
        with self.assertRaises(OllamaTimeout):
            client.installed_models()
        client, _ = client_with(HTTPResponse(200, {}, b"x" * (OllamaClient.MAX_RESPONSE_BYTES + 1)))
        with self.assertRaises(OllamaResponseTooLarge):
            client.installed_models()

    def test_input_limits_and_parameterized_json_payload(self):
        client, transport = client_with()
        with self.assertRaises(OllamaConfigurationError):
            client.generate("x" * (client.MAX_PROMPT_CHARS + 1), "qwen2.5:3b")
        with self.assertRaises(OllamaConfigurationError):
            client.generate("\x00", "qwen2.5:3b")
        # SQL-like content is ordinary JSON text, never interpolated into a URL or SQL statement.
        query = "'); DROP TABLE memories; --"
        client, transport = client_with(json_response({"models": [{"name": "qwen2.5:3b"}]}),
                                       json_response({"message": {"content": "safe"}, "done": True}))
        client.generate(query, "qwen2.5:3b")
        payload = json.loads(transport.requests[1][2].decode("utf-8"))
        self.assertEqual(payload["messages"][-1]["content"], query)
        self.assertEqual(transport.requests[1][1], "http://127.0.0.1:11434/api/chat")

    def test_real_transport_does_not_follow_redirect(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "http://example.com/api/tags")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = OllamaClient(f"http://127.0.0.1:{server.server_port}", ["qwen2.5:3b"], timeout=2)
            with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                with self.assertRaises(OllamaHTTPError):
                    client.installed_models()
            self.assertEqual(seen, ["/api/tags"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

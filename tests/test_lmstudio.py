import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from jarvis_core.lmstudio import LMStudioBionicClient
from jarvis_core.model_provider import (
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderModelUnavailable,
    ProviderProtocolError,
    ProviderTimeout,
)


class FakeTransport:
    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload or {}).encode("utf-8")
        self.error = error
        self.calls = []

    def request(self, url, body, timeout, max_response_bytes):
        self.calls.append((url, json.loads(body.decode("utf-8")), timeout, max_response_bytes))
        if self.error:
            raise self.error
        if len(self.payload) > max_response_bytes:
            from jarvis_core.model_provider import ProviderResponseTooLarge
            raise ProviderResponseTooLarge("too large")
        return self.status, self.payload


def client_with(payload=None, status=200, error=None):
    transport = FakeTransport(status=status, payload=payload, error=error)
    client = LMStudioBionicClient("http://127.0.0.1:1235", transport=transport)
    return client, transport


class LMStudioBionicTests(unittest.TestCase):
    def test_native_request_disables_reasoning_and_storage_and_returns_visible_text(self):
        client, transport = client_with({"output": [
            {"type": "reasoning", "content": "private reasoning"},
            {"type": "message", "content": "I’m well, thank you."},
        ]})
        result = client.generate("How are you?", "prism-ml/bonsai-27b", system_prompt="Be helpful")
        self.assertEqual(result.text, "I’m well, thank you.")
        self.assertTrue(result.done)
        url, payload, timeout, max_bytes = transport.calls[0]
        self.assertEqual(url, "http://127.0.0.1:1235/api/v1/chat")
        self.assertEqual(payload["model"], "prism-ml/bonsai-27b")
        self.assertEqual(payload["input"], "How are you?")
        self.assertEqual(payload["system_prompt"], "Be helpful")
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["reasoning"], "off")
        self.assertIs(payload["store"], False)
        self.assertEqual(payload["max_output_tokens"], 192)
        self.assertEqual(timeout, 120.0)
        self.assertGreater(max_bytes, 0)

    def test_configured_model_is_explicit_and_no_provider_discovery_occurs(self):
        client, transport = client_with({"output": [{"type": "message", "content": "ok"}]})
        client.generate("hello", "selected/local-model")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]["model"], "selected/local-model")
        self.assertNotIn("/models", transport.calls[0][0])

    def test_only_numeric_loopback_http_endpoint_is_accepted(self):
        for url in (
            "https://127.0.0.1:1235", "http://localhost:1235", "http://192.168.1.5:1235",
            "http://127.0.0.1", "http://user@127.0.0.1:1235",
            "http://127.0.0.1:1235/?target=remote", "http://127.0.0.1:1235/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ProviderConfigurationError):
                LMStudioBionicClient(url)
        self.assertEqual(LMStudioBionicClient("http://[::1]:1235")._endpoint,
                         "http://[::1]:1235/api/v1/chat")
        self.assertEqual(LMStudioBionicClient("http://127.0.0.1:1235/api/v1/chat")._endpoint,
                         "http://127.0.0.1:1235/api/v1/chat")

    def test_input_bounds_and_model_identifier_validation(self):
        client, transport = client_with({"output": [{"type": "message", "content": "ok"}]})
        for model in ("", "bad\nmodel", "x" * 129):
            with self.subTest(model=model), self.assertRaises(ProviderConfigurationError):
                client.generate("hello", model)
        with self.assertRaises(ProviderConfigurationError):
            client.generate("hello", "model", max_output_tokens=513)
        with self.assertRaises(ProviderConfigurationError):
            client.generate(" ", "model")
        self.assertEqual(transport.calls, [])

    def test_missing_model_connection_timeout_http_and_redirect_fail_safely(self):
        client, _ = client_with(status=404, payload={"error": "private path"})
        with self.assertRaises(ProviderModelUnavailable):
            client.generate("hello", "model")
        client, _ = client_with(status=302)
        with self.assertRaises(ProviderHTTPError):
            client.generate("hello", "model")
        client, _ = client_with(status=503)
        with self.assertRaises(ProviderHTTPError):
            client.generate("hello", "model")
        client, _ = client_with(error=ProviderTimeout("private timeout detail"))
        with self.assertRaises(ProviderTimeout):
            client.generate("hello", "model")
        client, _ = client_with(error=ProviderConnectionError("private endpoint"))
        with self.assertRaises(ProviderConnectionError):
            client.generate("hello", "model")

    def test_malformed_tool_and_invalid_outputs_are_rejected(self):
        invalid_responses = (
            b"not-json",
            {"output": "hello"},
            {"output": [{"type": "tool_call", "name": "shell"}]},
            {"output": [{"type": "other", "content": "x"}]},
            {"output": [{"type": "message", "content": 5}]},
            {"output": [{"type": "message", "content": " \n"}]},
        )
        for response in invalid_responses:
            payload = response if isinstance(response, bytes) else response
            client, _ = client_with(payload)
            with self.subTest(response=response), self.assertRaises(ProviderProtocolError):
                client.generate("hello", "model")

    def test_model_error_and_malformed_errors_do_not_expose_provider_body(self):
        client, _ = client_with(status=400, payload={"error": "private local file path"})
        with self.assertRaises(ProviderModelUnavailable) as caught:
            client.generate("hello", "missing")
        self.assertNotIn("private", str(caught.exception))
        client, _ = client_with(b"secret malformed data")
        with self.assertRaises(ProviderProtocolError) as caught:
            client.generate("hello", "model")
        self.assertNotIn("secret", str(caught.exception))

    def test_real_transport_does_not_follow_redirect(self):
        redirected_requests = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                redirected_requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "49")
                self.end_headers()
                self.wfile.write(b'{"output":[{"type":"message","content":"wrong"}]}')

            def log_message(self, *_args):
                return

        target = HTTPServer(("127.0.0.1", 0), TargetHandler)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/escaped")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                return

        redirect = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (target, redirect)]
        for thread in threads:
            thread.start()
        try:
            client = LMStudioBionicClient(f"http://127.0.0.1:{redirect.server_port}")
            with self.assertRaises(ProviderHTTPError):
                client.generate("hello", "model")
            self.assertEqual(redirected_requests, [])
        finally:
            for server in (target, redirect):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

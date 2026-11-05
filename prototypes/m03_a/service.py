"""M03-A compatibility spike: static mock UI plus a harmless loopback health check.

This is deliberately not the Jarvis application service. It exposes no private
data, database, memory, search, model, or write API.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit


HOST = "127.0.0.1"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
ROOT = Path(__file__).resolve().parent
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


class LocalPrototypeServer(ThreadingHTTPServer):
    """Ephemeral-threaded server bound to IPv4 loopback only."""

    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__((HOST, port), PrototypeHandler)
        self.expected_host = f"127.0.0.1:{self.server_address[1]}"


class PrototypeHandler(BaseHTTPRequestHandler):
    server: LocalPrototypeServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # The prototype has no user data, and request logging is unnecessary.
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        # Intentionally no Access-Control-Allow-Origin header.
        self.end_headers()
        self.wfile.write(body)

    def _host_is_expected(self) -> bool:
        return self.headers.get("Host", "").lower() == self.server.expected_host

    def do_GET(self) -> None:
        if not self._host_is_expected():
            self._send(421, b'{"error":"host_not_allowed"}', "application/json; charset=utf-8")
            self.close_connection = True
            return

        path = urlsplit(self.path).path
        if path == "/health":
            # This endpoint reports process health only; it never checks or
            # returns identity, workspace, database, or model state.
            payload = json.dumps(
                {"status": "ok", "prototype": "m03-a", "data_connected": False}
            ).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")
            return

        entry = STATIC_FILES.get(path)
        if entry is None:
            self._send(404, b'{"error":"not_found"}', "application/json; charset=utf-8")
            return
        filename, content_type = entry
        body = (ROOT / filename).read_bytes()
        self._send(200, body, content_type)

    def do_POST(self) -> None:
        self._reject_mutation()

    def do_PUT(self) -> None:
        self._reject_mutation()

    def do_PATCH(self) -> None:
        self._reject_mutation()

    def do_DELETE(self) -> None:
        self._reject_mutation()

    def do_OPTIONS(self) -> None:
        self._reject_mutation()

    def _reject_mutation(self) -> None:
        if not self._host_is_expected():
            self._send(421, b'{"error":"host_not_allowed"}', "application/json; charset=utf-8")
            self.close_connection = True
            return
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True


def make_server(port: int = 8765) -> LocalPrototypeServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    return LocalPrototypeServer(port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"M03-A mock prototype: http://{server.expected_host}/")
    print("Only static mock UI and /health are available. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

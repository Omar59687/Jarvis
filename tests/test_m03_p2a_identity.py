from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core.ollama import GenerationResult
from jarvis_core.storage import Database, Permission, WorkspaceKind
from prototypes.m03_b1.service import API_PREFIX, make_server


MODEL = "test-model"


class FakeGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, model, *, system_prompt=None, max_output_tokens=192):
        self.calls.append((prompt, model, system_prompt, max_output_tokens))
        return GenerationResult(model=model, text="Local reply", done=True)


class P2A1WorkspaceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Atlas", WorkspaceKind.PROJECT, "alice", "ws-atlas")
        self.db.create_workspace("Notes", WorkspaceKind.PERSONAL, "alice", "ws-notes")
        self.db.create_workspace("Bob private", WorkspaceKind.PERSONAL, "bob", "ws-bob")
        self.db.grant_permission("alice", "ws-atlas", "reader", Permission.READ)
        self.generator = FakeGenerator()
        self.server = self.start_server("alice")

    def tearDown(self):
        self.stop_server(self.server)
        self.temp.cleanup()

    def start_server(self, actor_id):
        server = make_server(
            0, generator=self.generator, model=MODEL, pairing_code="pair-code",
            actor_id=actor_id, database=self.db,
        )
        server._test_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server._test_thread.start()
        return server

    @staticmethod
    def stop_server(server):
        server.shutdown()
        server.server_close()
        server._test_thread.join(timeout=2)

    def request(self, server, path, *, method="GET", payload=None, cookie=None,
                csrf=None, origin: bool | str = True, host: bool | str = True):
        headers = {}
        if host:
            headers["Host"] = server.expected_host if host is True else host
        if origin:
            headers["Origin"] = server.expected_origin if origin is True else origin
        if cookie:
            headers["Cookie"] = cookie
        if csrf is not None:
            headers["X-Jarvis-CSRF"] = csrf
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(f"http://{server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def pair(self, server=None):
        server = server or self.server
        status, headers, body = self.request(
            server, f"{API_PREFIX}/session/pair", method="POST",
            payload={"pairing_code": server.pairing.terminal_code},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = json.loads(body)["csrf_token"]
        return cookie, csrf

    def test_pairing_binds_only_trusted_actor_and_never_returns_it(self):
        status, _, body = self.request(
            self.server, f"{API_PREFIX}/session/pair", method="POST",
            payload={"pairing_code": "pair-code", "actor_id": "bob"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})
        self.assertEqual(self.generator.calls, [])

        cookie, csrf = self.pair()
        session_id = cookie.split("=", 1)[1]
        record = self.server.sessions.resolve(session_id)
        self.assertEqual(record.actor_id, "alice")
        status, _, body = self.request(self.server, f"{API_PREFIX}/session",
                                       cookie=cookie)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["active_workspace_id"], None)
        self.assertNotIn("actor_id", payload)
        self.assertNotIn("principal_id", payload)
        self.assertEqual(payload["csrf_token"], csrf)

        status, _, body = self.request(
            self.server, f"{API_PREFIX}/chat", method="POST",
            payload={"message": "hello", "principal_id": "bob"},
            cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})
        self.assertEqual(self.generator.calls, [])

    def test_workspace_list_is_read_filtered_and_session_authenticated(self):
        status, _, body = self.request(self.server, f"{API_PREFIX}/workspaces")
        self.assertEqual(status, 401)
        cookie, _ = self.pair()
        status, headers, body = self.request(self.server, f"{API_PREFIX}/workspaces",
                                             cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        workspaces = json.loads(body)["workspaces"]
        self.assertEqual([item["id"] for item in workspaces], ["ws-atlas", "ws-notes"])
        self.assertEqual(set(workspaces[0]), {"id", "name", "kind"})
        self.assertNotIn("ws-bob", body.decode("utf-8"))
        self.assertEqual(self.generator.calls, [])

    def test_selection_requires_session_csrf_origin_and_database_read_permission(self):
        cookie, csrf = self.pair()
        path = f"{API_PREFIX}/session/workspace"
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "ws-atlas"}, cookie=cookie)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "csrf_rejected"})
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "ws-atlas"}, cookie=cookie,
                                       csrf=csrf, origin=False)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "origin_not_allowed"})
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "ws-bob"}, cookie=cookie,
                                       csrf=csrf)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "workspace_unavailable"})
        self.assertNotIn("Bob private", body.decode("utf-8"))
        denied_response = body
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "does-not-exist"}, cookie=cookie,
                                       csrf=csrf)
        self.assertEqual(status, 404)
        self.assertEqual(body, denied_response)
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "ws-atlas"}, cookie=cookie,
                                       csrf=csrf, host="attacker.example")
        self.assertEqual(status, 421)
        self.assertEqual(json.loads(body), {"error": "host_not_allowed"})
        status, _, body = self.request(
            self.server, path, method="PUT",
            payload={"workspace_id": "ws-atlas", "user_id": "bob"},
            cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})
        status, _, body = self.request(self.server, path, method="PUT",
                                       payload={"workspace_id": "ws-atlas"}, cookie=cookie,
                                       csrf=csrf)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"active_workspace_id": "ws-atlas"})
        status, _, body = self.request(self.server, f"{API_PREFIX}/session", cookie=cookie)
        self.assertEqual(json.loads(body)["active_workspace_id"], "ws-atlas")
        self.assertEqual(self.generator.calls, [])

    def test_read_only_principal_can_select_only_granted_workspace(self):
        reader_server = self.start_server("reader")
        try:
            cookie, csrf = self.pair(reader_server)
            status, _, body = self.request(reader_server, f"{API_PREFIX}/workspaces",
                                           cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in json.loads(body)["workspaces"]], ["ws-atlas"])
            status, _, body = self.request(
                reader_server, f"{API_PREFIX}/session/workspace", method="PUT",
                payload={"workspace_id": "ws-atlas"}, cookie=cookie, csrf=csrf,
            )
            self.assertEqual(status, 200)
            status, _, body = self.request(
                reader_server, f"{API_PREFIX}/session/workspace", method="PUT",
                payload={"workspace_id": "ws-notes"}, cookie=cookie, csrf=csrf,
            )
            self.assertEqual(status, 404)
        finally:
            self.stop_server(reader_server)

    def test_revoked_read_permission_clears_active_workspace_and_blocks_reselection(self):
        cookie, csrf = self.pair()
        status, _, _ = self.request(
            self.server, f"{API_PREFIX}/session/workspace", method="PUT",
            payload={"workspace_id": "ws-atlas"}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 200)
        self.db.revoke_permission("alice", "ws-atlas", "alice", Permission.READ)

        status, _, body = self.request(self.server, f"{API_PREFIX}/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["active_workspace_id"])
        status, _, body = self.request(self.server, f"{API_PREFIX}/workspaces", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in json.loads(body)["workspaces"]], ["ws-notes"])
        status, _, body = self.request(
            self.server, f"{API_PREFIX}/session/workspace", method="PUT",
            payload={"workspace_id": "ws-atlas"}, cookie=cookie, csrf=csrf,
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "workspace_unavailable"})

    def test_workspace_apis_are_disabled_without_actor_but_general_chat_is_unchanged(self):
        server = make_server(0, generator=self.generator, model=MODEL, pairing_code="legacy-code")
        server._test_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server._test_thread.start()
        try:
            cookie, csrf = self.pair(server)
            status, _, body = self.request(server, f"{API_PREFIX}/workspaces", cookie=cookie)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"error": "workspace_service_unavailable"})
            status, _, body = self.request(
                server, f"{API_PREFIX}/chat", method="POST", payload={"message": "hello"},
                cookie=cookie, csrf=csrf,
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["reply"], "Local reply")
        finally:
            self.stop_server(server)

    def test_actor_database_configuration_must_be_explicit_and_consistent(self):
        with self.assertRaises(ValueError):
            make_server(0, generator=self.generator, model=MODEL, database=self.db)
        with self.assertRaises(ValueError):
            make_server(0, generator=self.generator, model=MODEL, actor_id="  ", database=self.db)
        with self.assertRaises(ValueError):
            make_server(0, generator=self.generator, model=MODEL, actor_id="alice",
                        database=self.db, database_path=Path(self.temp.name) / "other.sqlite3")


if __name__ == "__main__":
    unittest.main()

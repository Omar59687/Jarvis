from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core import Database, GenerationResult, Permission, WorkspaceKind
from jarvis_core.missions import MissionService
from prototypes.m03_b1.service import API_PREFIX, make_server


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, model, **kwargs):
        self.calls.append((prompt, model, kwargs))
        return GenerationResult(model, "Unused", True)


class ProactivityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("First", WorkspaceKind.PROJECT, "alice", "ws-a")
        self.db.create_workspace("Second", WorkspaceKind.PROJECT, "alice", "ws-b")
        self.provider = _FakeProvider()
        self.server = make_server(0, generator=self.provider, model="test-model",
                                  actor_id="alice", database=self.db, pairing_code="http-code")
        self.server._test_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server._test_thread.start()
        status, headers, body = self.request("POST", f"{API_PREFIX}/session/pair",
                                             {"pairing_code": "http-code"})
        self.assertEqual(status, 200, body)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(body)["csrf_token"]
        self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server._test_thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, *, include_cookie=True,
                include_csrf=True, origin=None):
        headers = {"Host": self.server.expected_host,
                   "Origin": self.server.expected_origin if origin is None else origin}
        if include_cookie and getattr(self, "cookie", None):
            headers["Cookie"] = self.cookie
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if include_csrf and getattr(self, "csrf", None):
            headers["X-Jarvis-CSRF"] = self.csrf
        request = Request(f"http://{self.server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            with response:
                return response.code, response.headers, response.read()

    def test_api_requires_session_csrf_and_exact_origin(self):
        status, _, body = self.request("GET", f"{API_PREFIX}/proactivity", include_cookie=False)
        self.assertEqual(status, 401)
        status, _, body = self.request("PUT", f"{API_PREFIX}/proactivity/preferences",
                                       {"enabled": True}, include_csrf=False)
        self.assertEqual(status, 403)
        status, _, body = self.request("PUT", f"{API_PREFIX}/proactivity/preferences",
                                       {"enabled": True}, origin="http://attacker.invalid")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "origin_not_allowed")

    def test_authenticated_bilingual_inbox_actions_are_scope_bound_and_ack_only(self):
        mission = MissionService(self.db).create(
            "alice", "ws-a", "Synthetic private goal", idempotency_key="http-mission-create-00000000"
        )
        with self.db._connection() as connection:
            connection.execute("UPDATE missions SET updated_at='2025-01-01T00:00:00+00:00' WHERE id=?",
                               (mission.id,))
            connection.commit()
        status, _, body = self.request("PUT", f"{API_PREFIX}/proactivity/preferences",
                                       {"enabled": True, "cooldown_seconds": 0})
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["enabled"])
        self.server.proactivity.tick()
        status, _, body = self.request("GET", f"{API_PREFIX}/proactivity")
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertEqual(payload["workspace_id"], "ws-a")
        self.assertEqual(len(payload["suggestions"]), 1)
        suggestion = payload["suggestions"][0]
        self.assertNotIn("Synthetic private goal", body.decode("utf-8"))
        self.assertEqual(suggestion["reason_code"], "mission_aged")
        status, _, body = self.request(
            "POST", f"{API_PREFIX}/proactivity/suggestions/{suggestion['id']}/accept", {})
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertTrue(result["acknowledgment_only"])
        self.assertEqual(result["suggestion"]["state"], "accepted")
        self.assertEqual(MissionService(self.db).get("alice", mission.id).state, "draft")
        self.assertEqual(self.provider.calls, [])

        self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-b"})
        status, _, body = self.request("GET", f"{API_PREFIX}/proactivity")
        self.assertEqual(status, 200)
        second_workspace = json.loads(body)
        self.assertEqual(second_workspace["workspace_id"], "ws-b")
        self.assertEqual(second_workspace["suggestions"], [])
        status, _, body = self.request(
            "POST", f"{API_PREFIX}/proactivity/suggestions/{suggestion['id']}/dismiss", {})
        self.assertEqual(status, 404)

    def test_api_rejects_client_identity_and_invalid_preference_values(self):
        status, _, body = self.request("PUT", f"{API_PREFIX}/proactivity/preferences",
                                       {"enabled": True, "principal_id": "bob"})
        self.assertEqual(status, 400)
        status, _, body = self.request("PUT", f"{API_PREFIX}/proactivity/preferences",
                                       {"enabled": True, "check_interval_seconds": 1})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

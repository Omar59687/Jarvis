from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jarvis_core import Database, GenerationResult, Permission, WorkspaceKind
from jarvis_core.ingestion import FolderSynchronizer
from jarvis_core.retrieval import SearchIndex
from prototypes.m03_b1.service import API_PREFIX, make_server


class FakeProvider:
    def __init__(self, text="The code is Amber-7 [D1]."):
        self.text = text
        self.calls = []

    def generate(self, prompt, model, *, system_prompt=None, temperature=0.3,
                 max_output_tokens=192):
        self.calls.append((prompt, model, system_prompt, temperature, max_output_tokens))
        return GenerationResult(model, self.text, True)


class P2AWorkspaceChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = Database(self.base / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Synthetic Atlas", WorkspaceKind.PROJECT, "alice", "ws-a")
        self.db.create_workspace("Private", WorkspaceKind.PERSONAL, "bob", "ws-b")
        self.db.grant_permission("alice", "ws-a", "alice", Permission.WRITE)
        self.root_path = self.base / "source"
        self.root_path.mkdir()
        self.root = self.db.register_approved_root("alice", "ws-a", self.root_path)
        (self.root_path / "facts.txt").write_text("Fictional room code Amber-7", encoding="utf-8")
        FolderSynchronizer(self.db).sync("alice", "ws-a", self.root.id)
        self.provider = FakeProvider()
        self.server = make_server(0, generator=self.provider, model="test-model",
                                  actor_id="alice", database=self.db, pairing_code="one-time")
        self.server._test_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server._test_thread.start()
        status, headers, body = self.request("POST", f"{API_PREFIX}/session/pair",
                                             {"pairing_code": "one-time"})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(body)["csrf_token"]
        self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server._test_thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        headers = {"Host": self.server.expected_host, "Origin": self.server.expected_origin,
                   "Cookie": getattr(self, "cookie", "")}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["X-Jarvis-CSRF"] = getattr(self, "csrf", "")
        request = Request(f"http://{self.server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def test_authenticated_chat_returns_only_validated_provenance_cards(self):
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "old room code?"})
        response = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(response["outcome"], "valid_citations")
        self.assertEqual(response["citations"][0]["label"], "D1")
        self.assertEqual(response["citations"][0]["relative_path"], "facts.txt")
        self.assertNotIn(str(self.root_path), body.decode("utf-8"))
        self.assertEqual(self.provider.calls[0][3:], (0.0, 192))
        self.assertIn("untrusted", self.provider.calls[0][2])

    def test_invalid_citation_is_rejected_and_evidence_status_is_retained(self):
        self.provider.text = "The code is Amber-7 [M1]."
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "old room code?"})
        response = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(response["outcome"], "invalid_citations")
        self.assertTrue(response["abstained"])
        self.assertEqual(response["citations"][0]["source_status"], "active_document")
        self.assertFalse(response["candidate_creation_allowed"])

    def test_no_evidence_abstains_without_invoking_provider(self):
        self.provider.text = "Should never be used [D1]."
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "unmatched query xyz"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["outcome"], "no_authorized_evidence")
        self.assertEqual(self.provider.calls, [])

    def test_permission_revocation_clears_workspace_and_blocks_provider(self):
        self.db.revoke_permission("alice", "ws-a", "alice", Permission.READ)
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "workspace_unavailable"})
        self.assertEqual(self.provider.calls, [])

    def test_workspace_startup_requires_explicit_database_and_readable_actor(self):
        with self.assertRaises(ValueError):
            make_server(0, generator=self.provider, model="test-model", actor_id="alice")
        with self.assertRaises(ValueError):
            make_server(0, generator=self.provider, model="test-model", actor_id="nobody",
                        database=self.db)
        with self.assertRaises(ValueError):
            make_server(0, generator=self.provider, model="test-model", actor_id="alice",
                        database_path=self.base / "missing.sqlite3")


if __name__ == "__main__":
    unittest.main()

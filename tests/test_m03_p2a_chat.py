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
        self.on_generate = None

    def generate(self, prompt, model, *, system_prompt=None, temperature=0.3,
                 max_output_tokens=192):
        self.calls.append((prompt, model, system_prompt, temperature, max_output_tokens))
        if self.on_generate:
            self.on_generate()
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
        self.assertEqual(status, 200, body)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(body)["csrf_token"]
        self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server._test_thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, *, include_csrf=True):
        headers = {"Host": self.server.expected_host, "Origin": self.server.expected_origin,
                   "Cookie": getattr(self, "cookie", "")}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if include_csrf:
                headers["X-Jarvis-CSRF"] = getattr(self, "csrf", "")
        elif method == "DELETE" and include_csrf:
            headers["X-Jarvis-CSRF"] = getattr(self, "csrf", "")
        request = Request(f"http://{self.server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            with response:
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
        status, _, body = self.request("GET", f"{API_PREFIX}/memory-candidates")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["candidates"], [])

    def test_invalid_citation_is_rejected_and_evidence_status_is_retained(self):
        self.provider.text = "The code is Amber-7 [M1]."
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "old room code?"})
        response = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(response["outcome"], "invalid_citations")
        self.assertTrue(response["abstained"])
        self.assertEqual(response["evidence"][0]["source_status"], "active_document")
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

    def test_candidate_requires_explicit_review_and_snapshot_bound_approval(self):
        _, _, chat_body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        chat = json.loads(chat_body)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "The fictional room code was Amber-7.",
            "expected_workspace_id": "ws-a",
            "evidence_request_id": chat["evidence_request_id"], "citation_labels": ["D1"],
        })
        self.assertEqual(status, 201)
        candidate = json.loads(body)["candidate"]
        path = f"{API_PREFIX}/memory-candidates/{candidate['id']}"

        stale = {"expected_revision": candidate["revision"],
                 "content_sha256": "0" * 64, "evidence_sha256": candidate["evidence_sha256"],
                 "confirmed_snapshot": True, "expected_workspace_id": "ws-a"}
        status, _, _ = self.request("POST", path + "/approve", stale)
        self.assertEqual(status, 409)
        unconfirmed = {"expected_revision": candidate["revision"],
                       "content_sha256": candidate["content_sha256"],
                       "evidence_sha256": candidate["evidence_sha256"],
                       "expected_workspace_id": "ws-a"}
        status, _, body = self.request("POST", path + "/approve", unconfirmed)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "explicit_review_required")

        reviewed = dict(unconfirmed, confirmed_snapshot=True)
        status, _, body = self.request("POST", path + "/approve", reviewed)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "approved")
        self.assertTrue(json.loads(body)["memory_id"])

        status, _, body = self.request("POST", path + "/approve", reviewed)
        self.assertEqual(status, 409)
        status, _, body = self.request("GET", f"{API_PREFIX}/memory-candidates")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["candidates"], [])

    def test_approved_memory_lifecycle_is_end_to_end_and_pending_is_not_retrievable(self):
        _, _, chat_body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        chat = json.loads(chat_body)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "The fictional room code was Amber-7.",
            "expected_workspace_id": "ws-a",
            "evidence_request_id": chat["evidence_request_id"], "citation_labels": ["D1"],
        })
        self.assertEqual(status, 201)
        candidate = json.loads(body)["candidate"]
        self.assertEqual(candidate["state"], "pending")
        self.assertEqual(self.server.memory_service.search("alice", "ws-a", "Amber-7"), [])

        path = f"{API_PREFIX}/memory-candidates/{candidate['id']}"
        status, _, body = self.request("PUT", path, {
            "expected_revision": candidate["revision"],
            "expected_workspace_id": "ws-a",
            "content": "Reviewed: the fictional room code was Amber-7.",
            "retain_existing_sources": True,
        })
        self.assertEqual(status, 200)
        edited = json.loads(body)["candidate"]
        self.assertEqual(edited["revision"], candidate["revision"] + 1)
        self.assertEqual(self.server.memory_service.search("alice", "ws-a", "Amber-7"), [])

        status, _, body = self.request("GET", path)
        review = json.loads(body)["candidate"]
        decision = {
            "expected_revision": review["revision"],
            "content_sha256": review["content_sha256"],
            "evidence_sha256": review["evidence_sha256"],
            "confirmed_snapshot": True,
            "expected_workspace_id": "ws-a",
        }
        status, _, body = self.request("POST", path + "/approve", decision)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["memory_id"])
        retrieved = self.server.memory_service.search("alice", "ws-a", "Amber-7")
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0].source_status, "active_sources")

        self.provider.text = "The fictional room code was Amber-7 [M1]."
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {
            "message": "What was the fictional room code Amber-7?"
        })
        response = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(response["outcome"], "valid_citations")
        self.assertEqual(response["citations"][0]["label"], "M1")
        self.assertEqual(response["citations"][0]["source_status"], "active_sources")

    def test_candidate_edit_rebinds_provenance_and_stale_snapshot_cannot_approve(self):
        _, _, chat_body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        chat = json.loads(chat_body)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "Draft memory.", "expected_workspace_id": "ws-a",
            "evidence_request_id": chat["evidence_request_id"],
            "citation_labels": ["D1"],
        })
        candidate = json.loads(body)["candidate"]
        path = f"{API_PREFIX}/memory-candidates/{candidate['id']}"
        status, _, body = self.request("PUT", path, {
            "expected_revision": candidate["revision"], "content": "Reviewed memory.",
            "expected_workspace_id": "ws-a",
            "evidence_request_id": chat["evidence_request_id"], "citation_labels": ["D1"],
        })
        self.assertEqual(status, 200)
        edited = json.loads(body)["candidate"]
        self.assertEqual(edited["revision"], candidate["revision"] + 1)
        self.assertEqual(edited["evidence"], candidate["evidence"])
        old_decision = {"expected_revision": candidate["revision"],
                        "content_sha256": candidate["content_sha256"],
                        "evidence_sha256": candidate["evidence_sha256"],
                        "confirmed_snapshot": True, "expected_workspace_id": "ws-a"}
        status, _, _ = self.request("POST", path + "/approve", old_decision)
        self.assertEqual(status, 409)

    def test_candidate_cannot_be_created_from_memory_labels_or_other_workspace(self):
        self.db.create_workspace("Read only", WorkspaceKind.PERSONAL, "bob", "ws-read")
        self.db.grant_permission("bob", "ws-read", "alice", Permission.READ)
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace",
                                       {"workspace_id": "ws-b"})
        self.assertEqual(status, 404)
        _, _, chat_body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        chat = json.loads(chat_body)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "No M labels may become document provenance.",
            "expected_workspace_id": "ws-a",
            "evidence_request_id": chat["evidence_request_id"], "citation_labels": ["M1"],
        })
        self.assertEqual(status, 400)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "Actor identity must come from the session.", "user_authored": True,
            "expected_workspace_id": "ws-a",
            "actor_id": "bob",
        })
        self.assertEqual(status, 400)
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace",
                                       {"workspace_id": "ws-read"})
        self.assertEqual(status, 200)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "WRITE access is required.", "user_authored": True,
            "expected_workspace_id": "ws-read",
        })
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "write_permission_required"})

    def test_workspace_change_invalidates_prior_chat_evidence_handles(self):
        self.db.create_workspace("Second", WorkspaceKind.PROJECT, "bob", "ws-second")
        self.db.grant_permission("bob", "ws-second", "alice", Permission.READ)
        self.db.grant_permission("bob", "ws-second", "alice", Permission.WRITE)
        _, _, chat_body = self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})
        chat = json.loads(chat_body)
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace",
                                       {"workspace_id": "ws-second"})
        self.assertEqual(status, 200)
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "Cannot cite the prior workspace.",
            "expected_workspace_id": "ws-second",
            "evidence_request_id": chat["evidence_request_id"], "citation_labels": ["D1"],
        })
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "evidence_selection_required")

    def test_workspace_switch_during_generation_discards_old_workspace_answer(self):
        self.db.create_workspace("Second", WorkspaceKind.PROJECT, "bob", "ws-second")
        self.db.grant_permission("bob", "ws-second", "alice", Permission.READ)
        self.db.grant_permission("bob", "ws-second", "alice", Permission.WRITE)
        started = threading.Event()
        resume = threading.Event()
        self.provider.on_generate = lambda: (started.set(), resume.wait(timeout=3))
        result = []
        worker = threading.Thread(target=lambda: result.append(
            self.request("POST", f"{API_PREFIX}/chat", {"message": "room code?"})))
        worker.start()
        self.assertTrue(started.wait(timeout=3))
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace",
                                       {"workspace_id": "ws-second"})
        self.assertEqual(status, 200)
        resume.set()
        worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 409)
        self.assertEqual(json.loads(result[0][2]), {"error": "workspace_selection_changed"})

    def test_rejection_is_explicit_authenticated_and_not_replayable(self):
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "A user-authored synthetic preference.", "user_authored": True,
            "expected_workspace_id": "ws-a",
        }, include_csrf=False)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "csrf_rejected"})
        status, _, body = self.request("POST", f"{API_PREFIX}/memory-candidates", {
            "content": "A user-authored synthetic preference.", "user_authored": True,
            "expected_workspace_id": "ws-a",
        })
        candidate = json.loads(body)["candidate"]
        decision = {"expected_revision": candidate["revision"],
                    "content_sha256": candidate["content_sha256"],
                    "evidence_sha256": candidate["evidence_sha256"],
                    "confirmed_snapshot": True, "expected_workspace_id": "ws-a"}
        path = f"{API_PREFIX}/memory-candidates/{candidate['id']}/reject"
        status, _, body = self.request("POST", path, decision)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "rejected")
        status, _, body = self.request("POST", path, decision)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"], "candidate_changed")

    def test_workspace_can_be_cleared_to_return_to_general_chat(self):
        status, _, body = self.request("DELETE", f"{API_PREFIX}/session/workspace")
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["active_workspace_id"])
        status, _, body = self.request("POST", f"{API_PREFIX}/chat", {"message": "How are you?"})
        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertEqual(response["reply"], self.provider.text)
        self.assertNotIn("outcome", response)
        self.assertIn("no access to the user's memories", self.provider.calls[-1][2])


if __name__ == "__main__":
    unittest.main()

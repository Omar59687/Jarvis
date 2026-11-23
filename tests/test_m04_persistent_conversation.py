from __future__ import annotations

import json
import http.client
import sqlite3
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from jarvis_core import AccessDenied, Database, GenerationResult, Permission, WorkspaceKind
from jarvis_core.ingestion import FolderSynchronizer
from prototypes.m03_b1.service import API_PREFIX, make_server


MODEL = "test-local-model"


class MemoryProvider:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def generate(self, prompt, model, *, system_prompt=None, temperature=0.3,
                 max_output_tokens=192):
        self.calls.append((prompt, model, system_prompt, temperature, max_output_tokens))
        self.entered.set()
        if self.block:
            self.release.wait(timeout=5)
        return GenerationResult(model, "The local test value is Amber-7 [D1].", True)


class PersistentConversationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "jarvis.sqlite3"
        self.db = Database(self.path)
        self.db.initialize()
        self.db.create_workspace("Atlas", WorkspaceKind.PROJECT, "alice", "ws-a")
        self.db.create_workspace("Private", WorkspaceKind.PERSONAL, "bob", "ws-b")

    def tearDown(self):
        self.temp.cleanup()

    def test_opt_in_defaults_off_restart_retrieval_and_revoke_deletes_content(self):
        self.assertFalse(self.db.conversation_memory_settings("alice", "ws-a")["enabled"])
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])
        with self.assertRaises(RuntimeError):
            self.db.save_persistent_conversation_turn(
                "alice", "ws-a", conversation_id=None, expected_revision=1,
                user_text="must not save", assistant_text="must not save either",
            )

        enabled = self.db.set_conversation_memory_settings(
            "alice", "ws-a", enabled=True, retention_days=30,
        )
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["retention_days"], 30)
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])
        conversation_id = self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=enabled["revision"],
            user_text="Earlier question: what is the blue marker?",
            assistant_text="The blue marker is Cobalt-12.",
        )

        restarted = Database(self.path)
        self.assertEqual(restarted.current_version, 12)
        detail = restarted.get_persistent_conversation("alice", "ws-a", conversation_id)
        self.assertEqual(detail["turns"][0]["user"], "Earlier question: what is the blue marker?")
        revision, context = restarted.search_persistent_conversation_context(
            "alice", "ws-a", "Tell me about the blue marker.",
        )
        self.assertEqual(revision, enabled["revision"])
        self.assertEqual(context, [("Earlier question: what is the blue marker?", "The blue marker is Cobalt-12.")])

        revoked = restarted.set_conversation_memory_settings(
            "alice", "ws-a", enabled=False, retention_days=30,
        )
        self.assertFalse(revoked["enabled"])
        self.assertIsNotNone(revoked["revoked_at"])
        self.assertEqual(restarted.list_persistent_conversations("alice", "ws-a"), [])
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM persistent_conversation_turns").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM persistent_conversations").fetchone()[0], 0)
        finally:
            connection.close()

    def test_expiry_deletion_and_workspace_scope_are_enforced(self):
        setting = self.db.set_conversation_memory_settings("alice", "ws-a", enabled=True)
        conversation_id = self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=setting["revision"],
            user_text="expire-me historical secret", assistant_text="expired response",
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("UPDATE persistent_conversations SET expires_at=? WHERE id=?",
                               ("2000-01-01T00:00:00.000000+00:00", conversation_id))
            connection.execute("UPDATE persistent_conversation_turns SET expires_at=? WHERE conversation_id=?",
                               ("2000-01-01T00:00:00.000000+00:00", conversation_id))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM persistent_conversation_turns").fetchone()[0], 0)
        finally:
            connection.close()
        with self.assertRaises(AccessDenied):
            self.db.conversation_memory_settings("alice", "ws-b")
        with self.assertRaises(AccessDenied):
            self.db.search_persistent_conversation_context("alice", "ws-b", "secret")

        current = self.db.conversation_memory_settings("alice", "ws-a")
        first = self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=current["revision"],
            user_text="delete me", assistant_text="delete this reply",
        )
        second = self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=current["revision"],
            user_text="keep me", assistant_text="keep this reply",
        )
        self.assertTrue(self.db.delete_persistent_conversation("alice", "ws-a", first))
        self.assertIsNone(self.db.get_persistent_conversation("alice", "ws-a", first))
        self.assertIsNotNone(self.db.get_persistent_conversation("alice", "ws-a", second))
        self.assertFalse(self.db.delete_persistent_conversation("alice", "ws-a", "f" * 32))

    def test_historical_context_is_relevant_and_byte_bounded(self):
        settings = self.db.set_conversation_memory_settings("alice", "ws-a", enabled=True)
        self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=settings["revision"],
            user_text="The distinctive marker is saffron-83.",
            assistant_text="I will use saffron-83 only as untrusted conversational context.",
        )
        self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=settings["revision"],
            user_text="A later unrelated question about the weather.",
            assistant_text="The local forecast was sunny.",
        )
        revision, relevant = self.db.search_persistent_conversation_context(
            "alice", "ws-a", "What did we say about saffron-83?", limit=1,
        )
        self.assertEqual(revision, settings["revision"])
        self.assertEqual(len(relevant), 1)
        self.assertIn("saffron-83", relevant[0][0])
        _, bounded = self.db.search_persistent_conversation_context(
            "alice", "ws-a", "saffron-83", limit=6, max_bytes=256,
        )
        encoded = json.dumps(
            [{"user": user, "assistant": assistant} for user, assistant in bounded],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 256)

    def test_backup_restore_preserves_persistent_memory_schema_and_content(self):
        settings = self.db.set_conversation_memory_settings("alice", "ws-a", enabled=True)
        conversation_id = self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=settings["revision"],
            user_text="backup test question", assistant_text="backup test answer",
        )
        self.db.grant_permission("bob", "ws-b", "alice", Permission.MANAGE_WORKSPACE)
        backup = self.db.backup_to(self.root / "jarvis-backup.sqlite3", actor_id="alice")
        restored_path = self.root / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)
        restored = Database(restored_path)
        restored.initialize()
        detail = restored.get_persistent_conversation("alice", "ws-a", conversation_id)
        self.assertEqual(detail["turns"][0]["assistant"], "backup test answer")

    def test_consent_revocation_remains_available_after_write_permission_is_lost(self):
        settings = self.db.set_conversation_memory_settings("alice", "ws-a", enabled=True)
        self.db.save_persistent_conversation_turn(
            "alice", "ws-a", conversation_id=None, expected_revision=settings["revision"],
            user_text="consent data", assistant_text="consent response",
        )
        self.db.revoke_permission("alice", "ws-a", "alice", Permission.WRITE)
        with self.assertRaises(AccessDenied):
            self.db.set_conversation_memory_settings("alice", "ws-a", enabled=True)
        revoked = self.db.set_conversation_memory_settings("alice", "ws-a", enabled=False)
        self.assertFalse(revoked["enabled"])
        self.assertFalse(revoked["can_write"])
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])


class PersistentConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.create_workspace("Atlas", WorkspaceKind.PROJECT, "alice", "ws-a")
        self.db.create_workspace("Private", WorkspaceKind.PERSONAL, "bob", "ws-b")
        self.source = self.root / "approved"
        self.source.mkdir()
        (self.source / "facts.txt").write_text("The fictional atlas test value is Amber-7.", encoding="utf-8")
        root = self.db.register_approved_root("alice", "ws-a", self.source)
        FolderSynchronizer(self.db).sync("alice", "ws-a", root.id)
        self.provider = MemoryProvider()
        self._start_server("first-pair-code")
        self.cookie, self.csrf = self._pair("first-pair-code")
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})
        self.assertEqual(status, 200, body)

    def tearDown(self):
        self._stop_server()
        self.temp.cleanup()

    def _start_server(self, pairing_code):
        self.server = make_server(
            0, generator=self.provider, model=MODEL, actor_id="alice", database=self.db,
            pairing_code=pairing_code,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _stop_server(self):
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None

    def request(self, method, path, payload=None, *, cookie=None, csrf=True):
        headers = {"Host": self.server.expected_host, "Origin": self.server.expected_origin}
        if cookie is None:
            cookie = getattr(self, "cookie", "")
        if cookie:
            headers["Cookie"] = cookie
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf and getattr(self, "csrf", None):
            headers["X-Jarvis-CSRF"] = self.csrf
        request = Request(f"http://{self.server.expected_host}{path}", data=data,
                          headers=headers, method=method)
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            with response:
                return response.code, response.headers, response.read()

    def _pair(self, code):
        status, headers, body = self.request(
            "POST", f"{API_PREFIX}/session/pair", {"pairing_code": code}, cookie="", csrf=False,
        )
        self.assertEqual(status, 200, body)
        self.csrf = json.loads(body)["csrf_token"]
        return headers["Set-Cookie"].split(";", 1)[0], self.csrf

    def chat(self, message, *, voice_mode=False):
        return self.request("POST", f"{API_PREFIX}/chat", {
            "message": message, "voice_mode": voice_mode,
        })

    def test_default_off_opt_in_no_migration_voice_text_restart_and_delete(self):
        status, _, body = self.request("GET", f"{API_PREFIX}/conversation-memory")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["settings"]["enabled"])
        self.assertEqual(json.loads(body)["settings"]["retention_days"], 30)

        status, _, _ = self.chat("What is the atlas test value? Pre-opt-in marker old-copper-19")
        self.assertEqual(status, 200)
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])

        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])

        status, _, body = self.chat("Text turn after opt-in cobalt-21")
        self.assertEqual(status, 200, body)
        status, _, body = self.chat(
            "Voice transcript: what is the atlas test value? marker voice-blue-7", voice_mode=True,
        )
        self.assertEqual(status, 200, body)
        saved = self.db.list_persistent_conversations("alice", "ws-a")
        self.assertEqual(len(saved), 1)
        conversation_id = saved[0]["id"]
        detail = self.db.get_persistent_conversation("alice", "ws-a", conversation_id)
        self.assertEqual([turn["user"] for turn in detail["turns"]], [
            "Text turn after opt-in cobalt-21",
            "Voice transcript: what is the atlas test value? marker voice-blue-7",
        ])
        self.assertNotIn("old-copper-19", json.dumps(detail))

        self._stop_server()
        self._start_server("second-pair-code")
        self.cookie, self.csrf = self._pair("second-pair-code")
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})
        self.assertEqual(status, 200, body)
        status, _, body = self.chat("What is the atlas test value? Recall voice-blue-7 from earlier.")
        self.assertEqual(status, 200, body)
        self.assertIn("voice-blue-7", self.provider.calls[-1][0])

        status, _, body = self.request("GET", f"{API_PREFIX}/conversation-memory")
        self.assertEqual(status, 200, body)
        for saved in json.loads(body)["conversations"]:
            status, _, body = self.request(
                "DELETE", f"{API_PREFIX}/conversation-memory/conversations/{saved['id']}",
            )
            self.assertEqual(status, 200, body)
        self.assertIsNone(self.db.get_persistent_conversation("alice", "ws-a", conversation_id))
        before = len(self.provider.calls)
        status, _, body = self.chat("What is the atlas test value without old history?")
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.provider.calls), before + 1)
        self.assertNotIn("voice-blue-7", self.provider.calls[-1][0])

        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": False, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])

    def test_workspace_denial_csrf_and_storage_failure_are_fail_closed(self):
        status, _, body = self.request("GET", f"{API_PREFIX}/conversation-memory", cookie="", csrf=False)
        self.assertEqual(status, 401)
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        }, csrf=False)
        self.assertEqual(status, 403)

        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-b"})
        self.assertEqual(status, 404)
        status, _, body = self.request("PUT", f"{API_PREFIX}/session/workspace", {"workspace_id": "ws-a"})
        self.assertEqual(status, 200)
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        })
        self.assertEqual(status, 200)

        call_count = len(self.provider.calls)
        with patch.object(self.db, "conversation_memory_settings", side_effect=sqlite3.OperationalError("synthetic storage failure")):
            status, _, body = self.chat("must fail closed")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "conversation_memory_unavailable"})
        self.assertEqual(len(self.provider.calls), call_count)

    def test_dashboard_exposes_explicit_opt_in_and_saved_history_controls(self):
        status, _, page = self.request("GET", "/", csrf=False)
        self.assertEqual(status, 200)
        self.assertIn(b'id="conversation-memory-enabled"', page)
        self.assertIn(b'id="conversation-memory-retention-days"', page)
        self.assertIn(b'id="conversation-memory-list"', page)
        status, _, script = self.request("GET", "/app.js", csrf=False)
        self.assertEqual(status, 200)
        self.assertIn(b"saveConversationMemoryPreference", script)
        self.assertIn(b"conversationMemoryDetail", script)

    def test_revocation_during_generation_discards_stale_reply_and_prevents_resave(self):
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.provider.block = True
        self.provider.entered.clear()
        self.provider.release.clear()
        result = []

        def send_chat():
            result.append(self.chat("What is the atlas test value during pending revocation?"))

        worker = threading.Thread(target=send_chat)
        worker.start()
        self.assertTrue(self.provider.entered.wait(timeout=2), result)
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": False, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.provider.release.set()
        worker.join(timeout=5)
        self.provider.block = False
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 409)
        self.assertEqual(json.loads(result[0][2]), {"error": "conversation_memory_changed"})
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])

    def test_logout_during_generation_prevents_late_persistent_write(self):
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.provider.block = True
        self.provider.entered.clear()
        self.provider.release.clear()
        result = []
        old_cookie = self.cookie

        def send_chat():
            result.append(self.chat("What is the atlas test value during logout race?"))

        worker = threading.Thread(target=send_chat)
        worker.start()
        self.assertTrue(self.provider.entered.wait(timeout=2), result)
        status, _, body = self.request("DELETE", f"{API_PREFIX}/session", cookie=old_cookie)
        self.assertEqual(status, 200, body)
        self.provider.release.set()
        worker.join(timeout=5)
        self.provider.block = False
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 401)
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])

    def test_disconnected_opted_in_request_does_not_persist_turn(self):
        status, _, body = self.request("PUT", f"{API_PREFIX}/conversation-memory/settings", {
            "enabled": True, "retention_days": 30,
        })
        self.assertEqual(status, 200, body)
        self.provider.block = True
        self.provider.entered.clear()
        self.provider.release.clear()
        connection = http.client.HTTPConnection(self.server.expected_host, timeout=5)
        payload = json.dumps({
            "message": "What is the atlas test value in this interrupted request?",
        }).encode("utf-8")
        headers = {
            "Host": self.server.expected_host, "Origin": self.server.expected_origin,
            "Cookie": self.cookie, "X-Jarvis-CSRF": self.csrf,
            "Content-Type": "application/json", "Content-Length": str(len(payload)),
        }
        worker = threading.Thread(
            target=lambda: connection.request("POST", f"{API_PREFIX}/chat", body=payload,
                                              headers=headers), daemon=True,
        )
        worker.start()
        self.assertTrue(self.provider.entered.wait(timeout=2))
        if connection.sock is not None:
            connection.sock.shutdown(socket.SHUT_RDWR)
        connection.close()
        self.provider.release.set()
        worker.join(timeout=3)
        self.provider.block = False
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.db.list_persistent_conversations("alice", "ws-a"), [])


if __name__ == "__main__":
    unittest.main()

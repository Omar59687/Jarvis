from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from jarvis_core import AccessDenied, Database, Permission, WorkspaceKind
from jarvis_core.ingestion import FolderSynchronizer
from jarvis_core.memory import (
    DecisionContextIssuer,
    InvalidDecision,
    LifecycleConflict,
    MemoryService,
)
from jarvis_core.retrieval import SearchIndex


SIGNING_KEY = b"synthetic-m01-e-signing-key-material-32-bytes"


class FoundationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db_path = self.base / "jarvis.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.workspace = self.db.create_workspace(
            "Synthetic personal workspace", WorkspaceKind.PERSONAL, "alice", "ws-a"
        )
        self.other_workspace = self.db.create_workspace(
            "Synthetic project workspace", WorkspaceKind.PROJECT, "bob", "ws-b"
        )
        self.db.grant_permission("bob", "ws-b", "alice", Permission.MANAGE_WORKSPACE)
        self.db.grant_permission("alice", "ws-a", "reader", Permission.READ)
        self.source_root = self.base / "approved"
        self.source_root.mkdir()
        self.root = self.db.register_approved_root("alice", "ws-a", self.source_root)
        self.sync = FolderSynchronizer(self.db)
        self.search = SearchIndex(self.db)
        self.memories = MemoryService(self.db, SIGNING_KEY)
        self.issuer = DecisionContextIssuer(SIGNING_KEY)

    def decision(self, candidate, action="approve_candidate"):
        return self.issuer.issue(
            action=action,
            actor_id="alice",
            workspace_id="ws-a",
            resource_id=candidate.id,
            resource_version=candidate.revision,
            content_sha256=candidate.content_sha256,
            evidence_sha256=candidate.evidence_sha256,
        )

    def purge_decision(self, snapshot):
        return self.issuer.issue(
            action="purge_memory",
            actor_id="alice",
            workspace_id="ws-a",
            resource_id=snapshot.id,
            resource_version=snapshot.state_version,
            content_sha256=snapshot.content_sha256,
            evidence_sha256=snapshot.evidence_sha256,
        )

    def test_folder_to_memory_lifecycle_enforces_permissions_and_keeps_provenance(self):
        source_text = "Jarvis architecture notes — المعرفة والذاكرة"
        source = self.source_root / "before.md"
        source.write_text(source_text, encoding="utf-8")

        # The full path runs with network creation disabled; all fixtures are local.
        with patch.object(socket, "socket", side_effect=AssertionError("network used")):
            report = self.sync.sync("alice", "ws-a", self.root.id)
            self.assertEqual((report.status, report.added), ("completed", 1))
            result = self.search.search("alice", "ws-a", "architecture")[0]
            self.assertEqual(result.relative_path, "before.md")
            self.assertIn("architecture", result.excerpt)

            with self.assertRaises(AccessDenied):
                self.sync.sync("reader", "ws-a", self.root.id)
            self.assertEqual(
                self.search.search("reader", "ws-a", "architecture")[0].document_id,
                result.document_id,
            )
            with self.assertRaises(AccessDenied):
                self.search.search("alice", "ws-b", "architecture")
            with self.assertRaises(AccessDenied):
                self.search.search("outsider", "ws-a", "architecture")

            candidate = self.memories.propose(
                "alice", "ws-a", "Jarvis supports العربية and English", evidence_document_ids=(result.document_id,)
            )
            self.assertEqual(candidate.state, "pending")
            with self.assertRaises(AccessDenied):
                self.memories.inspect_candidate("outsider", "ws-a", candidate.id)
            with self.assertRaises(AccessDenied):
                self.memories.propose(
                    "alice", "ws-b", "cross-workspace evidence", evidence_document_ids=(result.document_id,)
                )
            with self.assertRaises(AccessDenied):
                self.memories.propose("reader", "ws-a", "reader cannot propose", user_authored=True)

            old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            with patch("jarvis_core.memory._now", return_value=old_time):
                expired = self.decision(candidate)
            with self.assertRaises(InvalidDecision):
                self.memories.approve("alice", "ws-a", candidate.id, expired)

            stale_candidate = self.memories.propose(
                "alice", "ws-a", "Source path change should stale this review", evidence_document_ids=(result.document_id,)
            )
            approved = self.memories.approve(
                "alice", "ws-a", candidate.id, self.decision(candidate)
            )
            self.assertEqual(approved.evidence[0].relative_path, "before.md")
            edited = self.memories.edit(
                "alice", "ws-a", approved.id, expected_revision=1,
                content="Approved memory with an edited Arabic note: تذكير",
            )
            self.assertEqual(edited.evidence, approved.evidence)
            revisions = self.memories.list_revisions("alice", "ws-a", approved.id)
            self.assertEqual([item.revision for item in revisions], [1, 2])
            self.assertEqual(revisions[1].evidence, approved.evidence)
            with self.assertRaises(AccessDenied):
                self.memories.edit("reader", "ws-a", approved.id, expected_revision=2, content="unauthorized")

            source.rename(self.source_root / "after.md")
            renamed = self.sync.sync("alice", "ws-a", self.root.id)
            self.assertEqual(renamed.renamed, 1)
            self.assertEqual(
                self.search.search("alice", "ws-a", "architecture")[0].relative_path,
                "after.md",
            )
            self.assertEqual(
                self.memories.get("alice", "ws-a", approved.id).evidence[0].relative_path,
                "before.md",
            )
            with self.assertRaises(LifecycleConflict):
                self.memories.approve(
                    "alice", "ws-a", stale_candidate.id, self.decision(stale_candidate)
                )

            (self.source_root / "active-source.txt").write_text(
                "separate active source", encoding="utf-8"
            )
            self.assertEqual(self.sync.sync("alice", "ws-a", self.root.id).added, 1)
            active_doc = self.search.search("alice", "ws-a", "separate")[0]
            candidate_before_revoke = self.memories.propose(
                "alice", "ws-a", "Revoked source cannot be approved",
                evidence_document_ids=(active_doc.document_id,),
            )

            (self.source_root / "after.md").unlink()
            self.assertEqual(self.sync.sync("alice", "ws-a", self.root.id).deleted, 1)
            self.assertEqual(self.search.search("alice", "ws-a", "architecture"), [])
            self.assertEqual(
                self.memories.get("alice", "ws-a", approved.id).evidence[0].source_sha256,
                approved.evidence[0].source_sha256,
            )
            self.db.revoke_approved_root("alice", "ws-a", self.root.id)
            self.assertEqual(self.search.search("alice", "ws-a", "architecture"), [])
            self.assertEqual(
                self.memories.get("alice", "ws-a", approved.id).evidence,
                approved.evidence,
            )
            # A new evidence-backed proposal cannot attach a revoked/deleted document.
            with self.assertRaises(AccessDenied):
                self.memories.propose(
                    "alice", "ws-a", "revoked evidence", evidence_document_ids=(result.document_id,)
                )
            with self.assertRaises(LifecycleConflict):
                self.memories.approve(
                    "alice", "ws-a", candidate_before_revoke.id,
                    self.decision(candidate_before_revoke),
                )

            deleted = self.memories.soft_delete(
                "alice", "ws-a", approved.id, expected_state_version=edited.state_version
            )
            self.assertEqual(self.memories.list_memories("reader", "ws-a"), [])
            with self.assertRaises(LifecycleConflict):
                self.memories.list_revisions("reader", "ws-a", approved.id)
            with self.assertRaises(AccessDenied):
                self.memories.list_deleted("reader", "ws-a")
            recovered = self.memories.restore(
                "alice", "ws-a", approved.id, expected_state_version=deleted.state_version
            )
            self.assertEqual(self.memories.get("alice", "ws-a", approved.id).evidence, approved.evidence)
            deleted_again = self.memories.soft_delete(
                "alice", "ws-a", approved.id, expected_state_version=recovered.state_version
            )
            snapshot = self.memories.purge_snapshot("alice", "ws-a", approved.id)
            purge_decision = self.purge_decision(snapshot)
            self.memories.purge("alice", "ws-a", approved.id, decision=purge_decision)
            with self.assertRaises(LifecycleConflict):
                self.memories.get("alice", "ws-a", approved.id)
            with self.assertRaises(LifecycleConflict):
                self.memories.purge("alice", "ws-a", approved.id, decision=purge_decision)

        with self.db._connection() as connection:
            counts = {
                event: connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE workspace_id = ? AND event_type = ?",
                    ("ws-a", event),
                ).fetchone()[0]
                for event in (
                    "source_root.registered", "source.sync.completed", "memory.proposed",
                    "memory.approved", "memory.edited", "memory.deleted",
                    "memory.restored", "memory.purged", "source_root.revoked",
                )
            }
            audit_text = " ".join(
                row[0] for row in connection.execute(
                    "SELECT metadata_json FROM audit_events WHERE workspace_id = ?", ("ws-a",)
                )
            )
            derivative_counts = (
                connection.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = ?", (approved.id,)).fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM memory_revisions WHERE memory_id = ?", (approved.id,)).fetchone()[0],
            )
        self.assertEqual(counts["source_root.registered"], 1)
        self.assertGreaterEqual(counts["source.sync.completed"], 3)
        self.assertEqual(counts["memory.approved"], 1)
        self.assertEqual(counts["memory.edited"], 1)
        self.assertEqual(counts["memory.deleted"], 2)
        self.assertEqual(counts["memory.restored"], 1)
        self.assertEqual(counts["memory.purged"], 1)
        self.assertEqual(counts["source_root.revoked"], 1)
        self.assertNotIn("Approved memory with an edited Arabic note", audit_text)
        self.assertEqual(derivative_counts, (0, 0))

    def test_combined_backup_restore_preserves_fts_evidence_and_revisions(self):
        text = "backup integration retrieval المعرفة Jarvis"
        (self.source_root / "backup.txt").write_text(text, encoding="utf-8")
        report = self.sync.sync("alice", "ws-a", self.root.id)
        self.assertEqual(report.added, 1)
        document = self.search.search("alice", "ws-a", "retrieval")[0]
        candidate = self.memories.propose(
            "alice", "ws-a", "Remember after restore: استعادة", evidence_document_ids=(document.document_id,)
        )
        memory = self.memories.approve(
            "alice", "ws-a", candidate.id, self.decision(candidate)
        )
        edited = self.memories.edit(
            "alice", "ws-a", memory.id, expected_revision=1, content="Restored revision two — الذاكرة"
        )
        backup = self.db.backup_to(self.base / "combined-backup.sqlite3", actor_id="alice")
        restored_path = self.base / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)

        restored_db = Database(restored_path)
        restored_search = SearchIndex(restored_db)
        restored_memories = MemoryService(restored_db, SIGNING_KEY)
        found = restored_search.search("alice", "ws-a", "retrieval")
        self.assertEqual([(item.document_id, item.relative_path) for item in found], [(document.document_id, "backup.txt")])
        restored_memory = restored_memories.get("alice", "ws-a", memory.id)
        self.assertEqual(restored_memory.content, edited.content)
        self.assertEqual(restored_memory.evidence, memory.evidence)
        revisions = restored_memories.list_revisions("alice", "ws-a", memory.id)
        self.assertEqual([item.content for item in revisions], [memory.content, edited.content])
        self.assertEqual(revisions[-1].evidence, memory.evidence)

    def test_concurrent_edits_and_stale_state_versions_fail_safely(self):
        candidate = self.memories.propose("alice", "ws-a", "concurrency", user_authored=True)
        memory = self.memories.approve(
            "alice", "ws-a", candidate.id, self.decision(candidate)
        )

        def edit(content):
            try:
                self.memories.edit(
                    "alice", "ws-a", memory.id, expected_revision=1, content=content
                )
                return "updated"
            except LifecycleConflict:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ("winner one", "winner two")))
        self.assertCountEqual(results, ["updated", "stale"])
        current = self.memories.get("alice", "ws-a", memory.id)
        with self.assertRaises(LifecycleConflict):
            self.memories.soft_delete(
                "alice", "ws-a", memory.id, expected_state_version=memory.state_version
            )
        self.assertEqual(current.revision, 2)


if __name__ == "__main__":
    unittest.main()

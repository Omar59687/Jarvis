from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_core.memory import (
    DecisionContext, DecisionContextIssuer, InvalidDecision, LifecycleConflict, MemoryService,
)
from jarvis_core.storage import AccessDenied, Database, DEFAULT_MIGRATIONS, Migration, Permission, WorkspaceKind

KEY = b"synthetic-test-key-that-is-at-least-32-bytes"


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Synthetic", WorkspaceKind.PERSONAL, "alice", "ws-a")
        self.db.create_workspace("Other", WorkspaceKind.PROJECT, "bob", "ws-b")
        self.folder = self.root / "approved"
        self.folder.mkdir()
        approved = self.db.register_approved_root("alice", "ws-a", self.folder)
        self.root_id = approved.id
        self._add_document("doc-a", "ws-a", self.root_id, "notes.md", "source text")
        self.service = MemoryService(self.db, KEY)
        self.issuer = DecisionContextIssuer(KEY)

    def tearDown(self):
        self.temp.cleanup()

    def _add_document(self, doc_id, workspace, root_id, path, text):
        import hashlib
        now = "2026-01-01T00:00:00+00:00"
        with self.db._connection() as c:
            c.execute("INSERT INTO ingested_documents(id,workspace_id,root_id,relative_path,content_sha256,extracted_text,size_bytes,mtime_ns,file_identity,first_seen_at,last_seen_at,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
                      (doc_id,workspace,root_id,path,hashlib.sha256(text.encode()).hexdigest(),text,len(text),1,None,now,now))
            c.commit()

    def _context(self, candidate, action="approve_candidate"):
        return self.issuer.issue(action=action,actor_id="alice",workspace_id="ws-a",
            resource_id=candidate.id,resource_version=candidate.revision,
            content_sha256=candidate.content_sha256,evidence_sha256=candidate.evidence_sha256)

    def _propose(self, content="Remember this", ids=("doc-a",), user_authored=False):
        return self.service.propose("alice","ws-a",content,evidence_document_ids=ids,user_authored=user_authored)

    def test_fresh_v4_and_v3_upgrade(self):
        with self.db._connection() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],4)
            self.assertIsNotNone(c.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone())
        legacy=self.root/"v3.sqlite3"
        v3=Database(legacy,migrations=self.db._migrations[:3]); v3.initialize()
        v3.create_workspace("Legacy",WorkspaceKind.PERSONAL,"legacy","legacy-ws")
        Database(legacy).initialize()
        with closing(sqlite3.connect(legacy)) as c, c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],4)

    def test_v3_to_v4_migration_rolls_back_on_failure(self):
        legacy=self.root/"rollback.sqlite3"
        v3=Database(legacy,migrations=DEFAULT_MIGRATIONS[:3]); v3.initialize()
        failing=Database(legacy,migrations=DEFAULT_MIGRATIONS[:3]+(
            Migration(4,("CREATE TABLE migration_probe(value TEXT)","INVALID SQL")),))
        with self.assertRaises(sqlite3.OperationalError): failing.initialize()
        with closing(sqlite3.connect(legacy)) as c, c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],3)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='migration_probe'").fetchone())

    def test_propose_approve_retrieve_and_unicode(self):
        text="تذكير: Jarvis stores العربية and English safely"
        cand=self._propose(text)
        self.assertEqual(cand.state,"pending")
        self.assertEqual(cand.content,text)
        self.assertEqual(self.service.list_memories("alice","ws-a"),[])
        approved=self.service.approve("alice","ws-a",cand.id,self._context(cand))
        self.assertEqual(approved.content,text)
        self.assertEqual(approved.evidence[0].relative_path,"notes.md")
        self.assertEqual(self.service.get("alice","ws-a",approved.id).content,text)
        self.assertEqual(approved.workspace_id,"ws-a")

    def test_reject_and_pending_rejected_excluded(self):
        cand=self._propose()
        self.service.reject("alice","ws-a",cand.id,self._context(cand,"reject_candidate"))
        self.assertEqual(self.service.list_memories("alice","ws-a"),[])
        with self.assertRaises(LifecycleConflict): self.service.inspect_candidate("alice","ws-a",cand.id)
        resolved=self.service.list_candidates("alice","ws-a",state="rejected")[0]
        self.assertIsNone(resolved.content)
        self.assertEqual(resolved.evidence,())
        with self.db._connection() as c:
            row=c.execute("SELECT content,state FROM memory_candidates WHERE id=?",(cand.id,)).fetchone()
            self.assertEqual(tuple(row),(None,"rejected"))

    def test_routine_sync_timestamp_does_not_stale_reviewed_evidence(self):
        cand=self._propose()
        decision=self._context(cand)
        with self.db._connection() as c:
            c.execute("UPDATE ingested_documents SET last_seen_at=? WHERE id=?",("2026-01-03T00:00:00+00:00","doc-a")); c.commit()
        approved=self.service.approve("alice","ws-a",cand.id,decision)
        self.assertEqual(approved.evidence[0].observed_at,"2026-01-01T00:00:00+00:00")

    def test_stale_candidate_content_or_evidence_rejected(self):
        cand=self._propose()
        context=self._context(cand)
        edited=self.service.edit_candidate("alice","ws-a",cand.id,expected_revision=1,content="Changed",evidence_document_ids=("doc-a",))
        with self.assertRaises(InvalidDecision): self.service.approve("alice","ws-a",cand.id,context)
        current=self._context(edited)
        with self.db._connection() as c:
            c.execute("UPDATE ingested_documents SET content_sha256=? WHERE id=?",("f"*64,"doc-a")); c.commit()
        with self.assertRaises(LifecycleConflict): self.service.approve("alice","ws-a",cand.id,current)

    def test_unauthorized_approval_cross_workspace_evidence_denied(self):
        cand=self._propose()
        bad=self.issuer.issue(action="approve_candidate",actor_id="bob",workspace_id="ws-a",resource_id=cand.id,
            resource_version=cand.revision,content_sha256=cand.content_sha256,evidence_sha256=cand.evidence_sha256)
        with self.assertRaises(AccessDenied): self.service.approve("bob","ws-a",cand.id,bad)
        with self.assertRaises(AccessDenied): self.service.propose("alice","ws-b","secret",evidence_document_ids=("doc-a",))
        with self.assertRaises(AccessDenied): self.service.get("alice","ws-b","no-leak-id")

    def test_signed_decision_required_and_actor_bound(self):
        cand=self._propose()
        with self.assertRaises(InvalidDecision): self.service.approve("alice","ws-a",cand.id,True)
        spoof=self.issuer.issue(action="approve_candidate",actor_id="mallory",workspace_id="ws-a",resource_id=cand.id,
            resource_version=cand.revision,content_sha256=cand.content_sha256,evidence_sha256=cand.evidence_sha256)
        with self.assertRaises(InvalidDecision): self.service.approve("alice","ws-a",cand.id,spoof)

    def test_edit_revision_evidence_and_audit_privacy(self):
        cand=self._propose("old private phrase")
        memory=self.service.approve("alice","ws-a",cand.id,self._context(cand))
        changed=self.service.edit("alice","ws-a",memory.id,expected_revision=1,content="new private phrase")
        self.assertEqual(changed.evidence,memory.evidence)
        history=self.service.list_revisions("alice","ws-a",memory.id)
        self.assertEqual([r.revision for r in history],[1,2])
        self.assertEqual(history[-1].evidence,memory.evidence)
        with self.db._connection() as c:
            audit=" ".join(r[0] for r in c.execute("SELECT metadata_json FROM audit_events WHERE resource_id=?",(memory.id,)))
        self.assertNotIn("private phrase",audit)

    def test_deleted_memory_history_requires_recovery_permission(self):
        self.db.grant_permission("alice", "ws-a", "reader", Permission.READ)
        cand=self._propose("deleted private revision")
        memory=self.service.approve("alice","ws-a",cand.id,self._context(cand))
        resolved=self.service.list_candidates("reader", "ws-a", state="approved")[0]
        self.assertIsNone(resolved.content)
        self.assertEqual(resolved.evidence,())
        current=self.service.edit("alice","ws-a",memory.id,expected_revision=1,content="replacement text")
        self.service.soft_delete("alice","ws-a",current.id,expected_state_version=current.state_version)

        with self.assertRaises(LifecycleConflict):
            self.service.list_revisions("reader", "ws-a", memory.id)
        with self.assertRaises(AccessDenied):
            self.service.list_deleted_revisions("reader", "ws-a", memory.id)
        with self.assertRaises(AccessDenied):
            self.service.list_deleted("reader", "ws-a")
        with self.assertRaises(LifecycleConflict):
            self.service.get("reader", "ws-a", memory.id)
        self.assertEqual(self.service.list_memories("reader", "ws-a"), [])

        deleted=self.service.list_deleted("alice", "ws-a")[0]
        self.assertEqual(deleted.id, memory.id)
        history=self.service.list_deleted_revisions("alice", "ws-a", memory.id)
        self.assertEqual([r.content for r in history], ["deleted private revision", "replacement text"])

    def test_soft_delete_restore_and_purge_confirmation(self):
        cand=self._propose()
        m=self.service.approve("alice","ws-a",cand.id,self._context(cand))
        deleted=self.service.soft_delete("alice","ws-a",m.id,expected_state_version=m.state_version)
        self.assertEqual(self.service.list_memories("alice","ws-a"),[])
        with self.assertRaises(LifecycleConflict): self.service.get("alice","ws-a",m.id)
        snapshot=self.service.purge_snapshot("alice","ws-a",m.id)
        restore_context=self.issuer.issue(action="purge_memory",actor_id="alice",workspace_id="ws-a",resource_id=m.id,
            resource_version=snapshot.state_version,content_sha256=snapshot.content_sha256,evidence_sha256=snapshot.evidence_sha256)
        with self.assertRaises(InvalidDecision): self.service.purge("alice","ws-a",m.id,decision=True)
        restored=self.service.restore("alice","ws-a",m.id,expected_state_version=deleted.state_version)
        self.assertEqual(self.service.get("alice","ws-a",m.id).evidence,m.evidence)
        deleted=self.service.soft_delete("alice","ws-a",m.id,expected_state_version=restored.state_version)
        snap=self.service.purge_snapshot("alice","ws-a",m.id)
        confirm=self.issuer.issue(action="purge_memory",actor_id="alice",workspace_id="ws-a",resource_id=m.id,
            resource_version=snap.state_version,content_sha256=snap.content_sha256,evidence_sha256=snap.evidence_sha256)
        self.service.purge("alice","ws-a",m.id,decision=confirm)
        with self.assertRaises(LifecycleConflict): self.service.get("alice","ws-a",m.id)
        with self.db._connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM memory_evidence WHERE memory_id=?",(m.id,)).fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM memory_revisions WHERE memory_id=?",(m.id,)).fetchone()[0],0)
            candidate=c.execute("SELECT content_sha256,evidence_sha256,approved_memory_id FROM memory_candidates WHERE id=?",(cand.id,)).fetchone()
            self.assertEqual(tuple(candidate),("","",None))

    def test_duplicate_request_and_concurrent_approval(self):
        a=self.service.propose("alice","ws-a","idempotent",evidence_document_ids=("doc-a",),request_key="key-1")
        b=self.service.propose("alice","ws-a","idempotent",evidence_document_ids=("doc-a",),request_key="key-1")
        self.assertEqual(a.id,b.id)
        with self.assertRaises(LifecycleConflict): self.service.propose("alice","ws-a","different",evidence_document_ids=("doc-a",),request_key="key-1")
        contexts=[self._context(a),self._context(a)]
        def approve(context):
            try: self.service.approve("alice","ws-a",a.id,context); return "ok"
            except LifecycleConflict: return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(approve,contexts))
        self.assertCountEqual(results,["ok","conflict"])
        self.assertEqual(len(self.service.list_memories("alice","ws-a")),1)
        with self.assertRaises(LifecycleConflict): self.service.propose("alice","ws-a","idempotent",evidence_document_ids=("doc-a",),request_key="key-1")

    def test_approval_decision_replay_denied(self):
        c=self._propose(); decision=self._context(c)
        self.service.approve("alice","ws-a",c.id,decision)
        with self.assertRaises(LifecycleConflict): self.service.approve("alice","ws-a",c.id,decision)

    def test_input_parameterization_and_offline(self):
        payload="x'); DROP TABLE memories;-- العربية"
        c=self._propose(payload,user_authored=True,ids=())
        self.assertEqual(c.content,payload)
        with self.assertRaises(ValueError): self.service.propose("alice","ws-a","\x00bad",user_authored=True)
        with self.assertRaises(ValueError): self.service.list_memories("alice","ws-a",limit=0)
        malformed=DecisionContext("approve_candidate","alice","ws-a",c.id,c.revision,
            c.content_sha256,c.evidence_sha256,"id","bad timestamp",123)
        with self.assertRaises(InvalidDecision): self.service.approve("alice","ws-a",c.id,malformed)
        with patch.object(socket,"socket",side_effect=AssertionError("network used")):
            got=self.service.inspect_candidate("alice","ws-a",c.id)
        self.assertEqual(got.content,payload)
        with self.db._connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone())

    def test_revoked_or_deleted_source_blocks_approval(self):
        cand=self._propose()
        self.db.revoke_approved_root("alice","ws-a",self.root_id)
        with self.assertRaises(LifecycleConflict): self.service.approve("alice","ws-a",cand.id,self._context(cand))

    def test_soft_deleted_source_blocks_new_approval(self):
        cand=self._propose()
        with self.db._connection() as c:
            c.execute("UPDATE ingested_documents SET deleted_at=? WHERE id=?",("2026-01-02T00:00:00+00:00","doc-a")); c.commit()
        with self.assertRaises(LifecycleConflict): self.service.approve("alice","ws-a",cand.id,self._context(cand))

    def test_backup_restore_v4_memories(self):
        self.db.grant_permission("bob","ws-b","alice",Permission.MANAGE_WORKSPACE)
        c=self._propose(); m=self.service.approve("alice","ws-a",c.id,self._context(c))
        backup=self.root/"backup.sqlite3"; self.db.backup_to(backup,"alice")
        restored=self.root/"restored.sqlite3"; Database.restore_from_backup(backup,restored)
        restored_db=Database(restored); service=MemoryService(restored_db,KEY)
        self.assertEqual(service.get("alice","ws-a",m.id).content,m.content)


if __name__ == "__main__": unittest.main()

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from jarvis_core import AccessDenied, Database, Permission, WorkspaceKind
from jarvis_core.ingestion import FolderSynchronizer
from jarvis_core.retrieval import (
    FTS5Unavailable,
    MAX_EXCERPT_CHARS,
    MAX_RESULTS,
    SearchIndex,
    fts5_available,
)
from jarvis_core.storage import DEFAULT_MIGRATIONS


class BilingualRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db_path = self.base / "jarvis.sqlite3"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.workspace = self.database.create_workspace(
            "Project", WorkspaceKind.PROJECT, "alice", "workspace-a"
        )
        self.root_path = self.base / "approved"
        self.root_path.mkdir()
        self.root = self.database.register_approved_root(
            "alice", self.workspace.id, self.root_path
        )
        self.synchronizer = FolderSynchronizer(self.database)
        self.search_index = SearchIndex(self.database)

    def add_file(self, name: str, content: str) -> None:
        (self.root_path / name).write_text(content, encoding="utf-8")
        self.synchronizer.sync("alice", self.workspace.id, self.root.id)

    def test_fts5_capability_probe_and_clear_failure(self) -> None:
        self.assertTrue(fts5_available())
        with patch("jarvis_core.retrieval.fts5_available", return_value=False):
            with self.assertRaisesRegex(FTS5Unavailable, "requires an SQLite build with FTS5"):
                self.search_index.search("alice", self.workspace.id, "notes")

    def test_arabic_and_english_keyword_retrieval(self) -> None:
        arabic = "ملاحظات المعرفة والذاكرة المحلية"
        english = "Jarvis local knowledge and memory"
        self.add_file("arabic.md", arabic)
        self.add_file("english.txt", english)
        ar = self.search_index.search("alice", self.workspace.id, "والذاكرة")
        en = self.search_index.search("alice", self.workspace.id, "knowledge")
        self.assertEqual([item.relative_path for item in ar], ["arabic.md"])
        self.assertEqual([item.relative_path for item in en], ["english.txt"])
        self.assertIn("والذاكرة", ar[0].excerpt)
        self.assertIn("knowledge", en[0].excerpt)

    def test_arabic_conjunction_prefix_is_not_stripped_or_stemmed(self) -> None:
        self.add_file("arabic-prefix.md", "ملاحظات المعرفة والذاكرة المحلية")
        with_prefix = self.search_index.search(
            "alice", self.workspace.id, "والذاكرة"
        )
        without_prefix = self.search_index.search(
            "alice", self.workspace.id, "الذاكرة"
        )
        self.assertEqual([item.relative_path for item in with_prefix], ["arabic-prefix.md"])
        self.assertEqual(without_prefix, [])

    def test_unchanged_sync_does_not_rebuild_search_index(self) -> None:
        self.add_file("stable.txt", "unchanged indexed text")
        traced_statements: list[str] = []
        original_connection = self.database._connection

        @contextmanager
        def traced_connection():
            with original_connection() as connection:
                connection.set_trace_callback(traced_statements.append)
                yield connection

        with patch.object(
            self.database, "_connection", side_effect=traced_connection
        ):
            report = self.synchronizer.sync(
                "alice", self.workspace.id, self.root.id
            )

        self.assertEqual((report.added, report.changed), (0, 0))
        normalized = [statement.upper() for statement in traced_statements]
        self.assertTrue(
            any("UPDATE INGESTED_DOCUMENTS SET LAST_SEEN_AT" in statement
                for statement in normalized)
        )
        self.assertFalse(
            any("DELETE FROM DOCUMENT_SEARCH" in statement
                for statement in normalized)
        )
        self.assertFalse(
            any("INSERT INTO DOCUMENT_SEARCH" in statement
                for statement in normalized)
        )

    def test_rank_order_is_stable_and_deterministic_for_ties(self) -> None:
        self.add_file("first.txt", "memory evidence")
        self.add_file("second.txt", "memory evidence")
        first = self.search_index.search("alice", self.workspace.id, "memory")
        second = self.search_index.search("alice", self.workspace.id, "memory")
        self.assertEqual(
            [(item.document_id, item.score) for item in first],
            [(item.document_id, item.score) for item in second],
        )
        self.assertEqual(
            [item.document_id for item in first],
            sorted(item.document_id for item in first),
        )

    def test_results_include_workspace_scoped_provenance_and_bounded_excerpt(self) -> None:
        self.add_file("source.md", "before distinctive evidence phrase after " * 80)
        results = self.search_index.search("alice", self.workspace.id, "distinctive")
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.root_id, self.root.id)
        self.assertEqual(result.relative_path, "source.md")
        self.assertTrue(result.document_id)
        self.assertLessEqual(len(result.excerpt), MAX_EXCERPT_CHARS)

    def test_workspace_isolation_and_direct_api_authorization(self) -> None:
        self.add_file("private.txt", "private orchard evidence")
        other = self.database.create_workspace(
            "Other", WorkspaceKind.PERSONAL, "bob", "workspace-b"
        )
        with self.assertRaises(AccessDenied):
            self.search_index.search("alice", other.id, "private")
        with self.assertRaises(AccessDenied):
            self.search_index.search("bob", self.workspace.id, "private")
        with self.assertRaises(AccessDenied):
            self.search_index.search("unknown", "missing-workspace", "")

        other_root_path = self.base / "other-approved"
        other_root_path.mkdir()
        other_root = self.database.register_approved_root(
            "bob", other.id, other_root_path
        )
        (other_root_path / "other.txt").write_text(
            "other workspace marker", encoding="utf-8"
        )
        FolderSynchronizer(self.database).sync("bob", other.id, other_root.id)
        self.database.grant_permission("bob", other.id, "alice", Permission.READ)
        self.assertEqual(
            self.search_index.search("alice", other.id, "private"),
            [],
        )
        self.assertEqual(
            [item.relative_path for item in self.search_index.search(
                "alice", other.id, "marker"
            )],
            ["other.txt"],
        )
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "marker"), [])
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "private")[0].relative_path, "private.txt")

    def test_index_tracks_added_changed_and_renamed_documents(self) -> None:
        path = self.root_path / "before.txt"
        path.write_text("oldmarker", encoding="utf-8")
        self.synchronizer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(
            self.search_index.search("alice", self.workspace.id, "oldmarker")[0].relative_path,
            "before.txt",
        )
        path.write_text("newmarker", encoding="utf-8")
        self.synchronizer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "oldmarker"), [])
        self.assertEqual(
            self.search_index.search("alice", self.workspace.id, "newmarker")[0].relative_path,
            "before.txt",
        )
        path.rename(self.root_path / "after.txt")
        self.synchronizer.sync("alice", self.workspace.id, self.root.id)
        result = self.search_index.search("alice", self.workspace.id, "newmarker")
        self.assertEqual(result[0].relative_path, "after.txt")

    def test_soft_deleted_and_revoked_source_documents_are_excluded(self) -> None:
        self.add_file("removed.txt", "tombstone marker")
        (self.root_path / "removed.txt").unlink()
        self.synchronizer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "tombstone"), [])
        self.add_file("revoked.txt", "revoked marker")
        self.assertEqual(len(self.search_index.search("alice", self.workspace.id, "revoked")), 1)
        self.assertTrue(
            self.database.revoke_approved_root("alice", self.workspace.id, self.root.id)
        )
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "revoked"), [])
        with self.database._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM document_search WHERE root_id = ?",
                    (self.root.id,),
                ).fetchone()[0],
                0,
            )

    def test_empty_malformed_long_and_sql_like_queries_are_safe(self) -> None:
        self.add_file("safe.txt", "safe evidence")
        self.assertEqual(self.search_index.search("alice", self.workspace.id, ""), [])
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "   !!! — ()"), [])
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "NEAR(,)"), [])
        self.assertEqual(
            self.search_index.search(
                "alice", self.workspace.id, '"; DROP TABLE workspaces; --'
            ),
            [],
        )
        with self.assertRaises(ValueError):
            self.search_index.search("alice", self.workspace.id, "x" * 513)
        with self.assertRaises(ValueError):
            self.search_index.search("alice", self.workspace.id, "\ud800")
        with self.assertRaises(ValueError):
            self.search_index.search("alice", self.workspace.id, "nul\x00byte")
        with self.assertRaises(ValueError):
            self.search_index.search("alice", self.workspace.id, "safe", limit=MAX_RESULTS + 1)
        with self.assertRaises(ValueError):
            self.search_index.search("alice", self.workspace.id, "safe", limit=0)
        with self.database._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
                    ("table", "workspaces"),
                ).fetchone()[0],
                1,
            )

    def test_rebuild_is_idempotent_and_workspace_scoped(self) -> None:
        self.add_file("one.txt", "rebuildable evidence")
        self.assertEqual(self.search_index.rebuild("alice", self.workspace.id), 1)
        self.assertEqual(self.search_index.rebuild("alice", self.workspace.id), 1)
        with self.database._connection() as connection:
            connection.execute(
                "DELETE FROM document_search WHERE workspace_id = ?",
                (self.workspace.id,),
            )
            connection.commit()
        self.assertEqual(self.search_index.search("alice", self.workspace.id, "rebuildable"), [])
        self.assertEqual(self.search_index.rebuild("alice", self.workspace.id), 1)
        self.assertEqual(
            len(self.search_index.search("alice", self.workspace.id, "rebuildable")), 1
        )

    def test_schema_v2_upgrade_populates_fts_index(self) -> None:
        path = self.base / "schema-v2.sqlite3"
        v2 = Database(path, migrations=DEFAULT_MIGRATIONS[:2])
        v2.initialize()
        workspace = v2.create_workspace(
            "Preserved", WorkspaceKind.PROJECT, "owner", "v2-workspace"
        )
        root_path = self.base / "v2-approved"
        root_path.mkdir()
        root = v2.register_approved_root("owner", workspace.id, root_path)
        (root_path / "existing.txt").write_text(
            "upgraded bilingual database", encoding="utf-8"
        )
        FolderSynchronizer(v2).sync("owner", workspace.id, root.id)
        upgraded = Database(path)
        results = SearchIndex(upgraded).search("owner", workspace.id, "upgraded")
        self.assertEqual([item.relative_path for item in results], ["existing.txt"])
        with upgraded._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_backup_restore_and_offline_search(self) -> None:
        self.add_file("backup.txt", "offline backup retrieval")
        backup = self.database.backup_to(self.base / "backup.sqlite3", actor_id="alice")
        restored_path = self.base / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)
        restored = SearchIndex(Database(restored_path))
        with patch.object(socket, "socket", side_effect=AssertionError("network attempted")):
            with patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network attempted"),
            ):
                results = restored.search("alice", self.workspace.id, "retrieval")
        self.assertEqual([item.relative_path for item in results], ["backup.txt"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis_core import (  # noqa: E402
    AccessDenied,
    Database,
    Permission,
    WorkspaceKind,
)
from jarvis_core.ingestion import FolderSynchronizer, SyncLimits  # noqa: E402


class FolderIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.database = Database(self.base / "jarvis.sqlite3")
        self.database.initialize()
        self.workspace = self.database.create_workspace(
            "Project", WorkspaceKind.PROJECT, "alice", "workspace-a"
        )
        self.root_path = self.base / "approved"
        self.root_path.mkdir()
        self.root = self.database.register_approved_root(
            "alice", self.workspace.id, self.root_path
        )
        self.syncer = FolderSynchronizer(self.database)

    def documents(self) -> list[dict[str, object]]:
        with self.database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM ingested_documents WHERE workspace_id = ? ORDER BY relative_path",
                (self.workspace.id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def events(self) -> list[dict[str, object]]:
        with self.database._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sync_events ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def windows_stat(self, path: Path):
        info = os.stat(path, follow_symlinks=False)
        if not info.st_dev or not info.st_ino:
            self.skipTest("filesystem does not expose a stable Windows file identity")
        return info

    def test_initial_sync_is_idempotent_and_round_trips_arabic_english(self) -> None:
        expected = "مرحبا بالعالم — Jarvis notes"
        (self.root_path / "note.md").write_text(expected, encoding="utf-8")
        first = self.syncer.sync("alice", self.workspace.id, self.root.id)
        second = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual((first.added, first.discovered, first.status), (1, 1, "completed"))
        self.assertEqual((second.added, second.changed, second.deleted), (0, 0, 0))
        self.assertEqual(self.documents()[0]["extracted_text"], expected)
        self.assertEqual(len(self.documents()), 1)

    def test_duplicate_and_changed_file_handling(self) -> None:
        source = self.root_path / "same.txt"
        source.write_text("original", encoding="utf-8")
        first = self.syncer.sync("alice", self.workspace.id, self.root.id)
        document_id = self.documents()[0]["id"]
        again = self.syncer.sync("alice", self.workspace.id, self.root.id)
        source.write_text("updated content", encoding="utf-8")
        changed = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(first.added, 1)
        self.assertEqual((again.added, again.changed), (0, 0))
        self.assertEqual((changed.changed, changed.added), (1, 0))
        self.assertEqual(self.documents()[0]["id"], document_id)
        self.assertEqual(self.documents()[0]["extracted_text"], "updated content")

    def test_deleted_and_renamed_files_preserve_provenance(self) -> None:
        file_path = self.root_path / "before.md"
        file_path.write_text("same content", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        original = self.documents()[0]
        file_path.rename(self.root_path / "after.md")
        rename = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(rename.renamed, 1)
        renamed = self.documents()[0]
        self.assertEqual(renamed["id"], original["id"])
        self.assertEqual(renamed["relative_path"], "after.md")
        (self.root_path / "after.md").unlink()
        deleted = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(deleted.deleted, 1)
        preserved = self.documents()[0]
        self.assertIsNotNone(preserved["deleted_at"])
        self.assertEqual(preserved["extracted_text"], "same content")
        kinds = [event["event_type"] for event in self.events()]
        self.assertIn("renamed", kinds)
        self.assertIn("deleted", kinds)

    def test_ambiguous_duplicate_content_is_not_guessed_as_rename(self) -> None:
        (self.root_path / "first.txt").write_text("duplicate", encoding="utf-8")
        (self.root_path / "second.txt").write_text("duplicate", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        old_ids = {item["id"] for item in self.documents()}
        (self.root_path / "first.txt").unlink()
        (self.root_path / "second.txt").unlink()
        (self.root_path / "new.txt").write_text("duplicate", encoding="utf-8")
        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.renamed, 0)
        self.assertEqual(report.added, 1)
        active = [item for item in self.documents() if item["deleted_at"] is None]
        self.assertEqual(len(active), 1)
        self.assertNotIn(active[0]["id"], old_ids)

    def test_ingested_document_and_events_persist_after_reopen(self) -> None:
        (self.root_path / "persist.txt").write_text("persisted locally", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        reopened = FolderSynchronizer(Database(self.base / "jarvis.sqlite3"))
        report = reopened.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual((report.added, report.changed), (0, 0))
        self.assertEqual(self.documents()[0]["extracted_text"], "persisted locally")

    def test_partial_scan_does_not_infer_deletions_and_error_hides_content(self) -> None:
        (self.root_path / "good.txt").write_text("retain me", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        (self.root_path / "invalid.txt").write_bytes(b"private-secret-\xff")
        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.deleted, 0)
        errors = [event for event in self.events() if event["event_type"] == "error"]
        self.assertTrue(any(event["error_code"] == "invalid_utf8" for event in errors))
        self.assertNotIn("private-secret", str(errors))
        good = next(item for item in self.documents() if item["relative_path"] == "good.txt")
        self.assertIsNone(good["deleted_at"])

    def test_partial_scan_does_not_infer_rename_from_unavailable_file(self) -> None:
        import jarvis_core.ingestion as ingestion

        old_path = self.root_path / "old.txt"
        old_path.write_text("identical content", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        original = self.documents()[0]

        # Simulate a file disappearing from enumeration while another readable
        # path with identical bytes is present in the same incomplete scan.
        old_path.rename(self.root_path / ".temporarily-unavailable")
        (self.root_path / "new.txt").write_text("identical content", encoding="utf-8")
        scanner_name = "_scan_windows" if os.name == "nt" else "_scan_posix"
        scan_files = getattr(ingestion, scanner_name)

        def partial_scan(path: Path, limits: SyncLimits):
            scan = scan_files(path, limits)
            scan.complete = False
            scan.errors.append(("old.txt", "file_read_error"))
            return scan

        with patch.object(ingestion, scanner_name, side_effect=partial_scan):
            report = self.syncer.sync("alice", self.workspace.id, self.root.id)

        self.assertEqual(report.status, "partial")
        self.assertEqual((report.renamed, report.added, report.deleted), (0, 1, 0))
        documents = self.documents()
        preserved = next(item for item in documents if item["id"] == original["id"])
        self.assertEqual(preserved, original)
        self.assertIsNone(preserved["deleted_at"])
        added = next(item for item in documents if item["relative_path"] == "new.txt")
        self.assertNotEqual(added["id"], original["id"])
        event_types = [event["event_type"] for event in self.events()]
        self.assertNotIn("renamed", event_types)
        self.assertIn("added", event_types)

    def test_secret_hidden_and_unsupported_files_are_not_indexed(self) -> None:
        (self.root_path / ".env").write_text("x=y", encoding="utf-8")
        (self.root_path / "credentials.txt").write_text("private", encoding="utf-8")
        (self.root_path / "image.png").write_bytes(b"not text")
        (self.root_path / "ordinary.md").write_text("allowed", encoding="utf-8")
        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.discovered, 1)
        self.assertEqual([doc["relative_path"] for doc in self.documents()], ["ordinary.md"])

    def test_size_depth_and_file_count_limits_bound_scan(self) -> None:
        (self.root_path / "large.txt").write_text("123456", encoding="utf-8")
        nested = self.root_path / "a" / "b"
        nested.mkdir(parents=True)
        (nested / "deep.txt").write_text("deep", encoding="utf-8")
        limits = SyncLimits(max_depth=1, max_files=1, max_directory_entries=100, max_file_bytes=5, max_total_bytes=20)
        report = FolderSynchronizer(self.database, limits).sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        self.assertLessEqual(report.discovered, 1)
        codes = {event["error_code"] for event in self.events() if event["event_type"] == "error"}
        self.assertTrue(codes & {"size_limit", "depth_limit"})

    def test_file_and_entry_limits_are_enforced(self) -> None:
        (self.root_path / "one.txt").write_text("one", encoding="utf-8")
        (self.root_path / "two.txt").write_text("two", encoding="utf-8")
        limits = SyncLimits(max_depth=8, max_files=1, max_directory_entries=10, max_file_bytes=20, max_total_bytes=40)
        report = FolderSynchronizer(self.database, limits).sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        self.assertLessEqual(report.discovered, 1)
        codes = {event["error_code"] for event in self.events() if event["event_type"] == "error"}
        self.assertIn("file_limit", codes)

    def test_directory_entry_limit_is_enforced(self) -> None:
        (self.root_path / "one.txt").write_text("one", encoding="utf-8")
        (self.root_path / "two.txt").write_text("two", encoding="utf-8")
        limits = SyncLimits(max_depth=8, max_files=10, max_directory_entries=1, max_file_bytes=20, max_total_bytes=40)
        report = FolderSynchronizer(self.database, limits).sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        codes = {event["error_code"] for event in self.events() if event["event_type"] == "error"}
        self.assertIn("entry_limit", codes)

    def test_backup_restore_preserves_ingested_provenance(self) -> None:
        (self.root_path / "note.txt").write_text("restorable text", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        backup = self.database.backup_to(self.base / "backup.sqlite3", actor_id="alice")
        restored_path = self.base / "restored.sqlite3"
        Database.restore_from_backup(backup, restored_path)
        with Database(restored_path)._connection() as connection:
            row = connection.execute(
                "SELECT extracted_text, relative_path FROM ingested_documents WHERE root_id = ?",
                (self.root.id,),
            ).fetchone()
        self.assertEqual((row["extracted_text"], row["relative_path"]), ("restorable text", "note.txt"))

    def test_symlink_file_and_directory_are_not_followed(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("outside secret", encoding="utf-8")
        try:
            (self.root_path / "linked.txt").symlink_to(secret)
            (self.root_path / "linked-dir").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.discovered, 0)
        self.assertEqual(self.documents(), [])
        self.assertEqual(report.status, "partial")

    @unittest.skipIf(os.name == "nt", "this race check exercises POSIX descriptor traversal")
    def test_root_replacement_between_scan_and_commit_is_rejected(self) -> None:
        import jarvis_core.ingestion as ingestion

        (self.root_path / "note.txt").write_text("old approved root", encoding="utf-8")
        scan = ingestion._scan_posix

        def scan_then_replace(path: Path, limits: SyncLimits):
            result = scan(path, limits)
            moved = self.base / "original-approved"
            path.rename(moved)
            path.mkdir()
            (path / "outside.txt").write_text("replacement", encoding="utf-8")
            return result

        with patch.object(ingestion, "_scan_posix", side_effect=scan_then_replace):
            with self.assertRaises(AccessDenied):
                self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.documents(), [])

    @unittest.skipUnless(os.name == "nt", "Windows root identity check is platform-specific")
    def test_windows_root_replacement_between_scan_and_commit_is_rejected(self) -> None:
        import jarvis_core.ingestion as ingestion

        (self.root_path / "note.txt").write_text("original root", encoding="utf-8")
        self.windows_stat(self.root_path)
        scan = ingestion._scan_windows

        def scan_then_replace(path: Path, limits: SyncLimits):
            result = scan(path, limits)
            moved = self.base / "original-approved"
            path.rename(moved)
            path.mkdir()
            (path / "replacement.txt").write_text("replacement root", encoding="utf-8")
            return result

        with patch.object(ingestion, "_scan_windows", side_effect=scan_then_replace):
            with self.assertRaises(AccessDenied):
                self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.documents(), [])

    @unittest.skipUnless(os.name == "nt", "Windows file metadata behavior is platform-specific")
    def test_windows_regular_text_file_is_ingested(self) -> None:
        path = self.root_path / "ordinary.txt"
        path.write_text("ordinary Windows text", encoding="utf-8")
        self.windows_stat(self.root_path)
        if self.windows_stat(path).st_nlink != 1:
            self.skipTest("filesystem does not report a single-link regular file")
        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual((report.status, report.discovered, report.added), ("completed", 1, 1))
        self.assertEqual(self.documents()[0]["extracted_text"], "ordinary Windows text")

    @unittest.skipUnless(os.name == "nt", "Windows file metadata behavior is platform-specific")
    def test_windows_file_identity_uses_path_and_handle_file_ids(self) -> None:
        import jarvis_core.ingestion as ingestion

        path = self.root_path / "identity.txt"
        path.write_text("identity check", encoding="utf-8")
        self.windows_stat(self.root_path)
        path_info = self.windows_stat(path)
        with path.open("rb") as handle:
            handle_info = os.fstat(handle.fileno())
        if not path_info.st_dev or not path_info.st_ino:
            self.skipTest("filesystem does not expose a stable Windows file identity")
        self.assertEqual((path_info.st_dev, path_info.st_ino), (handle_info.st_dev, handle_info.st_ino))
        self.assertEqual(path_info.st_nlink, 1)
        self.assertEqual(handle_info.st_nlink, 1)
        self.assertEqual(ingestion._identity(path_info), f"{path_info.st_dev}:{path_info.st_ino}")

        self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.documents()[0]["file_identity"], f"{handle_info.st_dev}:{handle_info.st_ino}")

    @unittest.skipUnless(os.name == "nt", "Windows hard-link behavior is platform-specific")
    def test_windows_hard_links_are_skipped_when_supported(self) -> None:
        original = self.root_path / "original.txt"
        linked = self.root_path / "linked.txt"
        original.write_text("shared file data", encoding="utf-8")
        self.windows_stat(self.root_path)
        if self.windows_stat(original).st_nlink != 1:
            self.skipTest("filesystem does not report a single-link regular file")
        try:
            os.link(original, linked)
        except OSError as exc:
            self.skipTest(f"Windows hard-link creation unavailable: {exc}")
        self.assertGreaterEqual(os.stat(original, follow_symlinks=False).st_nlink, 2)
        with original.open("rb") as handle:
            self.assertGreaterEqual(os.fstat(handle.fileno()).st_nlink, 2)

        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.discovered, 0)
        self.assertEqual(self.documents(), [])
        self.assertTrue(any(event["error_code"] == "hard_link_skipped" for event in self.events()))

    @unittest.skipUnless(os.name == "nt", "Windows symlink behavior is platform-specific")
    def test_windows_symlink_file_and_directory_are_not_followed(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("outside secret", encoding="utf-8")
        self.windows_stat(self.root_path)
        try:
            (self.root_path / "linked.txt").symlink_to(secret)
            (self.root_path / "linked-dir").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Windows symlink creation unavailable: {exc}")

        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.discovered, 0)
        self.assertEqual(self.documents(), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior is platform-specific")
    def test_windows_junction_is_not_followed(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside", encoding="utf-8")
        self.windows_stat(self.root_path)
        junction = self.root_path / "junction"
        command_processor = os.environ.get("COMSPEC", "cmd.exe")
        command = (
            f'"{command_processor}" /d /c mklink /J "{junction}" "{outside}"'
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"cmd mklink /J failed ({result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertTrue(junction.is_junction(), "mklink succeeded but did not create a junction")

        report = self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(report.discovered, 0)
        self.assertEqual(self.documents(), [])
        skipped = [
            (event["relative_path"], event["error_code"])
            for event in self.events()
            if event["event_type"] == "skipped"
        ]
        self.assertIn(("junction", "reparse_skipped"), skipped)

    def test_revocation_disables_automatic_sync_and_blocks_ingestion(self) -> None:
        (self.root_path / "note.txt").write_text("content", encoding="utf-8")
        self.syncer.configure_auto_sync("alice", self.workspace.id, self.root.id, True, 60)
        self.assertTrue(self.database.revoke_approved_root("alice", self.workspace.id, self.root.id))
        with self.database._connection() as connection:
            enabled = connection.execute("SELECT enabled FROM sync_preferences WHERE root_id = ?", (self.root.id,)).fetchone()[0]
        self.assertEqual(enabled, 0)
        with self.assertRaises(AccessDenied):
            self.syncer.sync("alice", self.workspace.id, self.root.id)

    def test_workspace_isolation_denies_other_actor_and_workspace(self) -> None:
        self.database.create_workspace("Other", WorkspaceKind.PERSONAL, "bob", "workspace-b")
        with self.assertRaises(AccessDenied):
            self.syncer.sync("bob", self.workspace.id, self.root.id)
        with self.assertRaises(AccessDenied):
            self.syncer.sync("alice", "workspace-b", self.root.id)

    def test_automatic_sync_is_due_bounded_and_offline(self) -> None:
        (self.root_path / "note.txt").write_text("local only", encoding="utf-8")
        self.syncer.configure_auto_sync("alice", self.workspace.id, self.root.id, True, 60)
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        with patch.object(socket, "socket", side_effect=AssertionError("network attempted")):
            with patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")):
                reports = self.syncer.run_due("alice", max_roots=1, now=future)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].added, 1)
        self.assertEqual(self.syncer.run_due("alice", max_roots=1, now=future), [])
        with self.assertRaises(ValueError):
            self.syncer.run_due("alice", max_roots=11, now=future)

    def test_audit_and_sync_events_persist_without_document_text(self) -> None:
        secretish = "do not put this text in event metadata"
        (self.root_path / "note.txt").write_text(secretish, encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        events = self.database.audit_events("alice", self.workspace.id)
        self.assertTrue(any(event["event_type"] == "source.sync.completed" for event in events))
        serialized = str(self.events()) + str(events)
        self.assertNotIn(secretish, serialized)

    def test_sql_injection_in_filename_is_only_data(self) -> None:
        name = "x'); DROP TABLE ingested_documents; --.txt"
        (self.root_path / name).write_text("safe", encoding="utf-8")
        self.syncer.sync("alice", self.workspace.id, self.root.id)
        self.assertEqual(self.documents()[0]["relative_path"], name)
        with self.database._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = ? AND name = ?",
                ("table", "ingested_documents"),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

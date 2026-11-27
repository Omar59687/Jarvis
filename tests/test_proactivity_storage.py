from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from jarvis_core.missions import MissionService
from jarvis_core.orchestration import TaskOrchestrationService
from jarvis_core.storage import DEFAULT_MIGRATIONS, Database, Migration, Permission, WorkspaceKind


class ProactivityStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "jarvis.sqlite3"
        self.db = Database(self.path)
        self.db.initialize()
        self.db.create_workspace("Proactivity", WorkspaceKind.PROJECT, "owner", "ws")
        self.tasks = TaskOrchestrationService(self.db)
        self.missions = MissionService(self.db, self.tasks)
        self.mission = self.missions.create(
            "owner", "ws", "Synthetic mission goal", idempotency_key="create-mission-key-000000000"
        )

    @staticmethod
    def _times() -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        return now.isoformat(), (now + timedelta(days=30)).isoformat()

    def _insert_suggestion(self, *, suggestion_id="suggestion-1", key="a" * 64,
                           workspace="ws", principal="owner", source_task=None,
                           source_mission=None, source_kind="task", state="active"):
        created, expires = self._times()
        source_task = source_task or self.mission.task_id
        with self.db._connection() as connection:
            connection.execute(
                "INSERT INTO proactive_suggestions(id,workspace_id,principal_id,suggestion_type,"
                "source_kind,source_task_id,source_mission_id,source_revision_sha256,"
                "deduplication_key,state,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (suggestion_id, workspace, principal,
                 "mission_follow_up" if source_kind == "mission" else "task_follow_up",
                 source_kind, source_task, source_mission,
                 "b" * 64, key, state, created, expires),
            )
            connection.commit()

    def test_fresh_initialization_and_v16_upgrade_preserve_data(self):
        with self.db._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 18)
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"proactivity_preferences", "proactive_suggestions",
                             "proactivity_suggestion_events"} <= tables)

        prior_path = Path(self.temp.name) / "v16.sqlite3"
        prior = Database(prior_path, migrations=DEFAULT_MIGRATIONS[:16])
        prior.initialize()
        prior.create_workspace("Preserved", WorkspaceKind.PROJECT, "owner", "prior-ws")
        with prior._connection() as connection:
            connection.execute("INSERT INTO audit_events(workspace_id,actor_id,event_type,"
                               "resource_type,resource_id,metadata_json,created_at) "
                               "VALUES('prior-ws','owner','test','test','1','{}','2026-01-01')")
            connection.commit()
            audit_count_before = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE workspace_id='prior-ws'").fetchone()[0]
        backup = Path(self.temp.name) / "v16-backup.sqlite3"
        restored_path = Path(self.temp.name) / "v16-restored.sqlite3"
        prior.backup_to(backup, "owner")
        Database.restore_from_backup(backup, restored_path)
        restored = Database(restored_path)
        restored.initialize()
        self.assertEqual(restored.current_version, 18)
        with restored._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE workspace_id='prior-ws'").fetchone()[0], audit_count_before)
        Database(prior_path).initialize()
        with Database(prior_path)._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 18)
            self.assertEqual(connection.execute("SELECT name FROM workspaces WHERE id='prior-ws'").fetchone()[0], "Preserved")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events WHERE workspace_id='prior-ws'").fetchone()[0], audit_count_before)

    def test_v18_migration_failure_rolls_back_ddl_and_version(self):
        path = Path(self.temp.name) / "rollback.sqlite3"
        v17 = Database(path, migrations=DEFAULT_MIGRATIONS[:-1])
        v17.initialize()
        v17.create_workspace("Preserved", WorkspaceKind.PROJECT, "owner", "rollback-ws")
        broken_migration = Migration(18, (*DEFAULT_MIGRATIONS[-1].statements[:1], "INVALID SQL"))
        broken = Database(path, migrations=(*DEFAULT_MIGRATIONS[:-1], broken_migration))
        with self.assertRaises(sqlite3.OperationalError):
            broken.initialize()
        with broken._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM pragma_table_info('proactive_suggestions') WHERE name='reason_code'").fetchone())
            self.assertEqual(connection.execute("SELECT name FROM workspaces WHERE id='rollback-ws'").fetchone()[0], "Preserved")

    def test_v17_upgrade_preserves_existing_proactivity_rows(self):
        path = Path(self.temp.name) / "v17.sqlite3"
        prior = Database(path, migrations=DEFAULT_MIGRATIONS[:-1])
        prior.initialize()
        prior.create_workspace("Preserved", WorkspaceKind.PROJECT, "owner", "v17-ws")
        prior_task = TaskOrchestrationService(prior).create_task(
            "owner", "v17-ws", "Synthetic preserved task",
            idempotency_key="v17-upgrade-create-task-000000")
        created, expires = self._times()
        with prior._connection() as connection:
            connection.execute(
                "INSERT INTO proactivity_preferences(workspace_id,principal_id,enabled,generation,"
                "check_interval_seconds,cooldown_seconds,created_at,updated_at) "
                "VALUES('v17-ws','owner',1,4,1800,86400,?,?)", (created, created))
            connection.execute(
                "INSERT INTO proactive_suggestions(id,workspace_id,principal_id,suggestion_type,"
                "source_kind,source_task_id,source_revision_sha256,deduplication_key,state,created_at,expires_at) "
                "VALUES('v17-suggestion','v17-ws','owner','task_follow_up','task',?,?,?,?,?,?)",
                (prior_task.id, "b" * 64, "a" * 64, "active", created, expires))
            connection.execute(
                "INSERT INTO proactivity_suggestion_events(suggestion_id,workspace_id,principal_id,"
                "source_task_id,event_type,created_at) VALUES('v17-suggestion','v17-ws','owner',?,"
                "'suggestion_created',?)", (prior_task.id, created))
            connection.commit()

        upgraded = Database(path)
        upgraded.initialize()
        with upgraded._connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 18)
            preference = connection.execute(
                "SELECT enabled,generation,check_interval_seconds FROM proactivity_preferences "
                "WHERE workspace_id='v17-ws' AND principal_id='owner'").fetchone()
            self.assertEqual(tuple(preference), (1, 4, 1800))
            suggestion = connection.execute(
                "SELECT state,reason_code FROM proactive_suggestions WHERE id='v17-suggestion'").fetchone()
            self.assertEqual(tuple(suggestion), ("active", "legacy"))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM proactivity_suggestion_events WHERE suggestion_id='v17-suggestion'").fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM proactivity_scheduler_claims WHERE workspace_id='v17-ws'").fetchone()[0], 0)

    def test_defaults_bounds_scope_uniqueness_and_fixed_suggestion_states(self):
        created, _ = self._times()
        with self.db._connection() as connection:
            connection.execute("INSERT INTO proactivity_preferences(workspace_id,principal_id,created_at,updated_at) VALUES('ws','owner',?,?)", (created, created))
            row = connection.execute("SELECT * FROM proactivity_preferences").fetchone()
            self.assertEqual((row["enabled"], row["generation"], row["check_interval_seconds"], row["cooldown_seconds"]), (0, 0, 3600, 86400))
            for values in ((900, 0), (86400, 2592000)):
                connection.execute("INSERT INTO proactivity_preferences(workspace_id,principal_id,check_interval_seconds,cooldown_seconds,created_at,updated_at) VALUES('ws',?,?,?,?,?)", ("principal-" + str(values[0]), *values, created, created))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO proactivity_preferences(workspace_id,principal_id,created_at,updated_at) VALUES('ws','owner',?,?)", (created, created))
            for interval, cooldown in ((899, 1), (86401, 1), (3600, -1), (3600, 2592001)):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO proactivity_preferences(workspace_id,principal_id,check_interval_seconds,cooldown_seconds,created_at,updated_at) VALUES('ws',?,?,?,?,?)", (f"bad-{interval}-{cooldown}", interval, cooldown, created, created))
            connection.commit()

        self._insert_suggestion()
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_suggestion(suggestion_id="bad-state", key="c" * 64, state="invented")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_suggestion(suggestion_id="duplicate-key", key="a" * 64)

    def test_source_foreign_keys_reject_missing_and_cross_scope_references(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_suggestion(source_task="missing-task")
        self.db.create_workspace("Other", WorkspaceKind.PROJECT, "other", "other-ws")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_suggestion(suggestion_id="cross-workspace", key="d" * 64,
                                    workspace="other-ws")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_suggestion(suggestion_id="wrong-mission", key="e" * 64,
                                    source_kind="mission", source_mission="missing-mission")

    def test_mission_source_reference_and_event_append_only(self):
        self._insert_suggestion(source_kind="mission", source_mission=self.mission.id)
        with self.db._connection() as connection:
            connection.execute("INSERT INTO proactivity_suggestion_events(suggestion_id,workspace_id,principal_id,source_task_id,event_type,metadata_json,created_at) VALUES('suggestion-1','ws','owner',?,'suggestion_created','{}','2026-01-01')", (self.mission.task_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE proactivity_suggestion_events SET metadata_json='{}' WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM proactivity_suggestion_events WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM proactive_suggestions WHERE id='suggestion-1'")
            connection.commit()

    def test_confirmed_task_purge_removes_suggestion_and_events(self):
        self._insert_suggestion()
        with self.db._connection() as connection:
            connection.execute("INSERT INTO proactivity_suggestion_events(suggestion_id,workspace_id,principal_id,source_task_id,event_type,created_at) VALUES('suggestion-1','ws','owner',?,'suggestion_created','2026-01-01')", (self.mission.task_id,))
            connection.commit()
        self.missions.cancel("owner", self.mission.id, idempotency_key="cancel-mission-key-000000000")
        self.missions.archive("owner", self.mission.id, idempotency_key="archive-mission-key-000000000")
        preview = self.tasks.preview_purge("owner", "ws", task_ids=(self.mission.task_id,), archived_only=True)
        self.tasks.purge_tasks("owner", "ws", task_ids=(self.mission.task_id,),
            expected_preview_sha256=preview.preview_sha256,
            confirmation=preview.confirmation_phrase,
            idempotency_key="purge-mission-key-000000000", archived_only=True)
        with self.db._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM proactive_suggestions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM proactivity_suggestion_events").fetchone()[0], 0)

    def test_backup_restore_preserves_proactivity_schema_and_rows(self):
        self._insert_suggestion()
        created, _ = self._times()
        with self.db._connection() as connection:
            connection.execute("INSERT INTO proactivity_preferences(workspace_id,principal_id,created_at,updated_at) VALUES('ws','owner',?,?)", (created, created))
            connection.commit()
        backup = Path(self.temp.name) / "backup.sqlite3"
        restored = Path(self.temp.name) / "restored.sqlite3"
        self.db.backup_to(backup, "owner")
        Database.restore_from_backup(backup, restored)
        reopened = Database(restored)
        reopened.initialize()
        with reopened._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM proactive_suggestions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM proactivity_preferences").fetchone()[0], 1)
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()

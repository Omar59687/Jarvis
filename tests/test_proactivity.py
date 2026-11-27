from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from jarvis_core.missions import MissionService
from jarvis_core.orchestration import TaskOrchestrationService
from jarvis_core.proactivity import (
    DEFAULT_CHECK_INTERVAL_SECONDS,
    MAX_ACTIVE_SUGGESTIONS,
    MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK,
    MAX_SCANNED_RECORDS_PER_SCOPE,
    ProactivityError,
    ProactivityService,
)
from jarvis_core.storage import Database, Permission, WorkspaceKind


def _key(label: str) -> str:
    return (label + "_" * 32)[:32]


class ProactivityServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "jarvis.sqlite3")
        self.db.initialize()
        self.db.create_workspace("Proactivity", WorkspaceKind.PROJECT, "owner", "ws")
        self.tasks = TaskOrchestrationService(self.db)
        self.missions = MissionService(self.db, self.tasks)
        self.mission_index = 0
        self.now = datetime.now(timezone.utc)
        self.service = ProactivityService(self.db, clock=lambda: self.now)

    def create_mission(self):
        self.mission_index += 1
        return self.missions.create(
            "owner", "ws", f"Synthetic mission goal {self.mission_index}",
            idempotency_key=_key(f"mission-{self.mission_index}"),
        )

    def enable(self, *, cooldown=0, interval=DEFAULT_CHECK_INTERVAL_SECONDS):
        return self.service.set_preferences(
            "owner", "ws", enabled=True,
            check_interval_seconds=interval, cooldown_seconds=cooldown,
        )

    def make_aged(self, mission):
        old = (self.now - timedelta(days=2)).isoformat()
        with self.db._connection() as connection:
            connection.execute("UPDATE missions SET updated_at=? WHERE id=?", (old, mission.id))
            connection.commit()

    def test_preferences_are_disabled_by_default_and_stop_fences_generation(self):
        preferences = self.service.get_preferences("owner", "ws")
        self.assertFalse(preferences.enabled)
        self.assertTrue(preferences.can_enable)
        self.assertEqual(preferences.check_interval_seconds, 3600)
        self.assertEqual(preferences.cooldown_seconds, 86400)
        enabled = self.enable(cooldown=120)
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.generation, 1)
        self.assertEqual(enabled.cooldown_seconds, 120)
        stopped = self.service.stop("owner", "ws")
        self.assertFalse(stopped.enabled)
        self.assertEqual(stopped.generation, 2)
        with self.db._connection() as connection:
            claim = connection.execute("SELECT * FROM proactivity_scheduler_claims").fetchone()
        self.assertIsNone(claim["claim_token"])
        self.assertIsNone(claim["next_check_at"])

    def test_preferences_update_persists_changed_cooldown_seconds(self):
        first = self.enable(cooldown=120, interval=1800)
        self.assertEqual(first.cooldown_seconds, 120)
        self.assertEqual(first.check_interval_seconds, 1800)
        second = self.enable(cooldown=0, interval=900)
        self.assertEqual(second.cooldown_seconds, 0)
        self.assertEqual(second.check_interval_seconds, 900)
        self.assertEqual(second.generation, 2)
        fetched = self.service.get_preferences("owner", "ws")
        self.assertEqual(fetched.cooldown_seconds, 0)
        self.assertEqual(fetched.check_interval_seconds, 900)

    def test_preferences_reject_boolean_integer_and_out_of_range_configuration(self):
        for kwargs in (
            {"enabled": 1},
            {"enabled": True, "check_interval_seconds": 899},
            {"enabled": True, "check_interval_seconds": 86401},
            {"enabled": True, "cooldown_seconds": -1},
            {"enabled": True, "cooldown_seconds": 2592001},
        ):
            with self.assertRaises(ValueError):
                self.service.set_preferences("owner", "ws", **kwargs)

    def test_detects_aged_and_actually_paused_missions(self):
        aged = self.create_mission()
        paused = self.create_mission()
        self.make_aged(aged)
        with self.db._connection() as connection:
            connection.execute("UPDATE missions SET state='paused' WHERE id=?", (paused.id,))
            connection.commit()
        self.enable(cooldown=0)
        result = self.service.tick()
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["created"], 2)
        values = {item.reason_code for item in self.service.list_suggestions("owner", "ws")}
        self.assertEqual(values, {"mission_aged", "mission_paused"})

    def test_detects_actual_blocked_task_without_treating_it_as_mission_state(self):
        mission = self.create_mission()
        with self.db._connection() as connection:
            connection.execute("UPDATE missions SET state='completed' WHERE id=?", (mission.id,))
            connection.execute("UPDATE orchestration_tasks SET state='blocked',updated_at=? WHERE id=?",
                               (self.now.isoformat(), mission.task_id))
            connection.commit()
        self.enable(cooldown=0)
        result = self.service.tick()
        self.assertEqual(result["created"], 1)
        suggestion = self.service.list_suggestions("owner", "ws")[0]
        self.assertEqual(suggestion.reason_code, "task_blocked")
        self.assertEqual(suggestion.source_kind, "task")

    def test_lifecycle_acceptance_is_acknowledgment_only_and_snooze_expires(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        self.service.tick()
        suggestion = self.service.list_suggestions("owner", "ws")[0]
        snoozed = self.service.act("owner", "ws", suggestion.id, "snooze", snooze_seconds=60)
        self.assertEqual(snoozed.state, "snoozed")
        self.now += timedelta(seconds=61)
        active = self.service.list_suggestions("owner", "ws")[0]
        self.assertEqual(active.state, "active")
        accepted = self.service.act("owner", "ws", active.id, "accept")
        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(self.missions.get("owner", mission.id).state, "draft")
        with self.db._connection() as connection:
            events = [row[0] for row in connection.execute(
                "SELECT event_type FROM proactivity_suggestion_events ORDER BY id")]
        self.assertEqual(events, ["suggestion_created", "suggestion_snoozed",
                                  "suggestion_unsnoozed", "suggestion_accepted"])

    def test_changed_source_evidence_is_retracted_before_display_or_action(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        self.service.tick()
        suggestion = self.service.list_suggestions("owner", "ws")[0]
        with self.db._connection() as connection:
            connection.execute("UPDATE orchestration_tasks SET updated_at=? WHERE id=?",
                               ((self.now + timedelta(seconds=1)).isoformat(), mission.task_id))
            connection.commit()
        self.assertEqual(self.service.list_suggestions("owner", "ws"), ())
        with self.db._connection() as connection:
            state = connection.execute("SELECT state FROM proactive_suggestions WHERE id=?",
                                       (suggestion.id,)).fetchone()[0]
        self.assertEqual(state, "retracted")

    def test_permission_revocation_disables_and_regrant_does_not_reactivate(self):
        self.db.grant_permission("owner", "ws", "alice", Permission.READ)
        self.db.grant_permission("owner", "ws", "alice", Permission.WRITE)
        service = ProactivityService(self.db, clock=lambda: self.now)
        enabled = service.set_preferences("alice", "ws", enabled=True)
        self.assertTrue(enabled.enabled)
        self.db.revoke_permission("owner", "ws", "alice", Permission.WRITE)
        with self.db._connection() as connection:
            row = connection.execute("SELECT enabled,generation FROM proactivity_preferences "
                                     "WHERE workspace_id='ws' AND principal_id='alice'").fetchone()
        self.assertEqual((row["enabled"], row["generation"]), (0, 2))
        self.db.grant_permission("owner", "ws", "alice", Permission.WRITE)
        restored = service.get_preferences("alice", "ws")
        self.assertFalse(restored.enabled)
        self.assertTrue(restored.can_enable)
        self.assertEqual(service.tick()["created"], 0)

    def test_generation_fence_rejects_worker_after_stop(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        entered = threading.Event()
        release = threading.Event()
        original = self.service._scan

        def blocked_scan(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args)

        result = []
        with patch.object(self.service, "_scan", side_effect=blocked_scan):
            thread = threading.Thread(target=lambda: result.append(self.service.tick()))
            thread.start()
            self.assertTrue(entered.wait(3))
            self.service.stop("owner", "ws")
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["stale_worker"], 1)
        self.assertEqual(self.service.list_suggestions("owner", "ws"), ())

    def test_concurrent_ticks_claim_scope_once_and_deduplicate(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        services = [ProactivityService(self.db, clock=lambda: self.now) for _ in range(2)]
        results = []
        threads = [threading.Thread(target=lambda svc=svc: results.append(svc.tick()))
                   for svc in services]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(len(self.service.list_suggestions("owner", "ws")), 1)

    def test_expired_scheduler_lease_is_recovered_by_new_service_instance(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        service = ProactivityService(self.db, clock=lambda: self.now, lease_seconds=1)
        with patch.object(service, "_scan", side_effect=RuntimeError("simulated process loss")):
            with self.assertRaisesRegex(RuntimeError, "simulated process loss"):
                service.tick()
        self.now += timedelta(seconds=2)
        restarted = ProactivityService(self.db, clock=lambda: self.now, lease_seconds=1)
        self.assertEqual(restarted.tick()["created"], 1)

    def test_scheduler_bounds_scanning_new_items_and_active_inbox(self):
        for _ in range(MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK + 2):
            mission = self.create_mission()
            with self.db._connection() as connection:
                connection.execute("UPDATE missions SET state='paused' WHERE id=?", (mission.id,))
                connection.commit()
        self.enable(cooldown=0)
        first = self.service.tick()
        self.assertLessEqual(first["scanned"], MAX_SCANNED_RECORDS_PER_SCOPE)
        self.assertEqual(first["created"], MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK)
        self.now += timedelta(seconds=DEFAULT_CHECK_INTERVAL_SECONDS)
        second = self.service.tick()
        self.assertEqual(second["created"], 2)
        self.assertEqual(len(self.service.list_suggestions("owner", "ws")),
                         MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK + 2)
        with self.assertRaises(ValueError):
            ProactivityService(self.db, scopes_per_tick=11)

    def test_active_suggestion_cap_and_retention_expiry(self):
        mission = self.create_mission()
        self.make_aged(mission)
        self.enable(cooldown=0)
        with self.db._connection() as connection:
            source = self.service._source_row(connection, "ws", "owner", mission.task_id, mission.id)
            digest = self.service._source_digest(source)
            created = self.now.isoformat()
            expires = (self.now + timedelta(days=30)).isoformat()
            for index in range(MAX_ACTIVE_SUGGESTIONS):
                connection.execute(
                    "INSERT INTO proactive_suggestions(id,workspace_id,principal_id,suggestion_type,"
                    "source_kind,source_task_id,source_mission_id,source_revision_sha256,"
                    "deduplication_key,state,created_at,expires_at,reason_code) "
                    "VALUES(?,?,?,'mission_follow_up','mission',?,?,?,?,'active',?,?,'mission_aged')",
                    (f"existing-{index}", "ws", "owner", mission.task_id, mission.id,
                     digest, f"{index:064x}", created, expires),
                )
            connection.commit()
        self.assertEqual(self.service.tick()["created"], 0)
        with self.db._connection() as connection:
            connection.execute("UPDATE proactive_suggestions SET state='active' ")
            connection.commit()
        self.now += timedelta(days=31)
        self.assertEqual(self.service.list_suggestions("owner", "ws"), ())
        with self.db._connection() as connection:
            expired = connection.execute("SELECT COUNT(*) FROM proactive_suggestions "
                                         "WHERE state='expired'").fetchone()[0]
        self.assertEqual(expired, MAX_ACTIVE_SUGGESTIONS)


if __name__ == "__main__":
    unittest.main()

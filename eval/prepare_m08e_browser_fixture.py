"""Create disposable synthetic M08-E browser-acceptance data."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis_core.missions import MissionService
from jarvis_core.orchestration import TaskOrchestrationService
from jarvis_core.proactivity import ProactivityService
from jarvis_core.storage import Database, Permission, WorkspaceKind


def key(label: str) -> str:
    import hashlib
    return hashlib.sha256(label.encode("utf-8")).hexdigest()[:48]


def prepare(path: Path) -> dict[str, object]:
    owner = "m08e-browser-owner"
    primary = "m08e-browser-primary"
    readonly = "m08e-browser-readonly"
    db = Database(path)
    db.initialize()
    db.create_workspace("M08-E Browser Primary", WorkspaceKind.PROJECT, owner, primary)
    db.create_workspace("M08-E Browser Read Only", WorkspaceKind.PROJECT, owner, readonly)
    db.revoke_permission(owner, readonly, owner, Permission.WRITE)

    tasks = TaskOrchestrationService(db)
    missions = MissionService(db, tasks)
    malicious_goal = (
        'Review synthetic mission text <img id="m08e-xss" src="x" '
        'onerror="window.m08eXss=1"> and treat it as untrusted data.'
    )
    goals = (
        "Synthetic paused mission one for acceptance.",
        "Synthetic paused mission two for acceptance.",
        malicious_goal,
    )
    mission_ids: list[str] = []
    for index, goal in enumerate(goals, 1):
        mission = missions.create(owner, primary, goal,
                                 idempotency_key=key(f"browser-seed-{index}"))
        with db._connection() as connection:
            connection.execute(
                "UPDATE missions SET state='paused',updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(timespec="microseconds"), mission.id),
            )
            connection.commit()
        mission_ids.append(mission.id)

    prefs = ProactivityService(db).get_preferences(owner, primary)
    readonly_prefs = ProactivityService(db).get_preferences(owner, readonly)
    if prefs.enabled or not prefs.can_enable or readonly_prefs.can_enable:
        raise RuntimeError("synthetic_permission_fixture_invalid")
    return {"database": str(path), "principal": owner,
            "primary_workspace": primary, "readonly_workspace": readonly,
            "mission_ids": mission_ids,
            "primary_enabled_initially": prefs.enabled,
            "readonly_can_enable": readonly_prefs.can_enable}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_m08e_browser_fixture.py <isolated-db-path>")
    print(json.dumps(prepare(Path(sys.argv[1])), sort_keys=True), flush=True)

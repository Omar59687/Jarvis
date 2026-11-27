"""Mission facade over persistent task planning and deterministic simulation.

Missions add their own exact-digest authorization and finite accounting. They do
not add a model, worker, process, filesystem, network, or external-action path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from .orchestration import (
    DeterministicFakePlanner, DeterministicFakeWorker, OrchestrationError,
    TaskOrchestrationService, TaskPlanningRequest, _canonical_json, _digest, _idempotency,
)
from .mission_planning import MissionPlanResult, MissionPlanningService
from .storage import Database, Permission, _now, _nonempty

MAX_MISSIONS_PER_WORKSPACE = 100
MAX_MISSION_STEPS = 25
MAX_MISSION_REQUESTS = 100
MAX_MISSION_RETRIES = 10
MAX_MISSION_SECONDS = 30 * 24 * 60 * 60
_EVENTS = {
    "mission_created", "plan_linked", "mission_authorized", "mission_rejected",
    "mission_paused", "mission_resumed", "mission_cancelled", "mission_interrupted",
    "mission_simulation_step", "mission_completed", "mission_archived", "mission_restored",
}


@dataclass(frozen=True)
class Mission:
    id: str
    task_id: str
    workspace_id: str
    principal_id: str
    state: str
    plan_revision: int
    authorized_revision: int | None
    authorization_sha256: str | None
    max_steps: int
    max_requests: int
    max_retries: int
    max_duration_seconds: int
    steps_used: int
    requests_used: int
    retries_used: int
    started_at: str | None
    expires_at: str
    created_at: str
    updated_at: str
    archived_at: str | None


class MissionService:
    """Manager-controlled facade that delegates planning/simulation to P4."""

    def __init__(self, database: Database,
                 tasks: TaskOrchestrationService | None = None) -> None:
        self.db = database
        self.tasks = tasks or TaskOrchestrationService(database)

    @staticmethod
    def _row(row) -> Mission:
        try:
            state = row["state"]
            if state not in {"draft", "awaiting_authorization", "authorized", "running",
                             "paused", "blocked", "interrupted", "completed", "cancelled", "expired"}:
                raise ValueError
            for name, maximum in (("max_steps", MAX_MISSION_STEPS),
                                  ("max_requests", MAX_MISSION_REQUESTS),
                                  ("max_retries", MAX_MISSION_RETRIES),
                                  ("max_duration_seconds", MAX_MISSION_SECONDS)):
                if type(row[name]) is not int or row[name] < (0 if name == "max_retries" else 1) or row[name] > maximum:
                    raise ValueError
            if (row["steps_used"] > row["max_steps"]
                    or row["requests_used"] > row["max_requests"]
                    or row["retries_used"] > row["max_retries"]):
                raise ValueError
            return Mission(*(row[key] for key in (
                "id", "task_id", "workspace_id", "principal_id", "state", "plan_revision",
                "authorized_revision", "authorization_sha256", "max_steps", "max_requests",
                "max_retries", "max_duration_seconds", "steps_used", "requests_used",
                "retries_used", "started_at", "expires_at", "created_at", "updated_at", "archived_at")))
        except (KeyError, TypeError, ValueError):
            raise OrchestrationError("mission_evidence_corrupt") from None

    def _require(self, actor_id: str, mission_id: str, *, write: bool = False,
                 manage: bool = False) -> Mission:
        actor_id = _nonempty(actor_id, "actor ID")
        with self.db._connection() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id=?", (_nonempty(mission_id, "mission ID"),)).fetchone()
            if row is None:
                raise OrchestrationError("mission_unavailable")
            permission = Permission.WRITE if write else Permission.READ
            Database._require_access(conn, actor_id, row["workspace_id"], permission)
            manager = self.db.check_access(actor_id, row["workspace_id"], Permission.MANAGE_WORKSPACE)
            if actor_id != row["principal_id"] and not manager:
                raise OrchestrationError("mission_unavailable")
            if manage and not manager:
                raise OrchestrationError("mission_manager_approval_required")
            return self._row(row)

    @staticmethod
    def _metadata(metadata: dict[str, str] | None) -> str:
        value = metadata or {}
        if (not isinstance(value, dict) or len(value) > 20
                or any(not isinstance(k, str) or len(k) > 40
                       or not isinstance(v, str) or len(v) > 256 for k, v in value.items())):
            raise ValueError("mission event metadata exceeds its bound")
        rendered = _canonical_json(value)
        if len(rendered.encode("utf-8")) > 2048:
            raise ValueError("mission event metadata exceeds its bound")
        return rendered

    def _event(self, conn: sqlite3.Connection, mission: Mission, actor: str,
               event: str, key: str, *, revision: int | None = None,
               digest: str | None = None, metadata: dict[str, str] | None = None) -> None:
        if event not in _EVENTS:
            raise ValueError("unsupported mission event")
        conn.execute("INSERT INTO mission_events(mission_id,workspace_id,principal_id,actor_id,event_type,"
                     "plan_revision,snapshot_sha256,idempotency_key,metadata_json,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (mission.id, mission.workspace_id, mission.principal_id, actor, event,
                      revision, digest, key, self._metadata(metadata), _now()))

    def get(self, actor_id: str, mission_id: str) -> Mission:
        mission = self._require(actor_id, mission_id)
        self.tasks.get_task(actor_id, mission.task_id)
        return mission

    def list(self, actor_id: str, workspace_id: str, *, limit: int = 100,
             include_archived: bool = False) -> tuple[Mission, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= 100 or type(include_archived) is not bool:
            raise ValueError("mission list bounds are invalid")
        with self.db._connection() as conn:
            Database._require_access(conn, actor_id, workspace_id, Permission.READ)
            manager = self.db.check_access(actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
            sql = "SELECT * FROM missions WHERE workspace_id=?"
            params: list[object] = [workspace_id]
            if not manager:
                sql += " AND principal_id=?"
                params.append(actor_id)
            if not include_archived:
                sql += " AND archived_at IS NULL"
            sql += " ORDER BY updated_at DESC,id LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return tuple(self._row(row) for row in rows)

    def create(self, actor_id: str, workspace_id: str, goal: str, *,
               idempotency_key: str, max_steps: int = 25, max_requests: int = 25,
               max_retries: int = 0, max_duration_seconds: int = 3600,
               lifetime_seconds: int = MAX_MISSION_SECONDS) -> Mission:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        key = _idempotency(idempotency_key)
        limits = (max_steps, max_requests, max_retries, max_duration_seconds, lifetime_seconds)
        maxima = (MAX_MISSION_STEPS, MAX_MISSION_REQUESTS, MAX_MISSION_RETRIES,
                  MAX_MISSION_SECONDS, MAX_MISSION_SECONDS)
        minima = (1, 1, 0, 1, 1)
        if any(type(value) is not int or value < low or value > high
               for value, low, high in zip(limits, minima, maxima)):
            raise ValueError("mission limit is outside its finite bound")
        self.db.require_access(actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
        goal_hash = _digest(goal.strip())
        limits = {"max_steps": max_steps, "max_requests": max_requests,
                  "max_retries": max_retries, "max_duration_seconds": max_duration_seconds,
                  "lifetime_seconds": lifetime_seconds}
        limits_hash = _digest(_canonical_json(limits))
        with self.db._connection() as conn:
            existing = conn.execute("SELECT m.id,e.metadata_json FROM missions m JOIN mission_events e ON e.mission_id=m.id "
                                     "WHERE e.workspace_id=? AND e.actor_id=? AND e.idempotency_key=? "
                                     "AND e.event_type='mission_created'", (workspace_id, actor_id, key)).fetchone()
        if existing:
            mission = self.get(actor_id, existing["id"])
            task = self.tasks.get_task(actor_id, mission.task_id)
            try:
                metadata = json.loads(existing["metadata_json"])
            except (ValueError, TypeError):
                raise OrchestrationError("mission_evidence_corrupt") from None
            if task.goal_sha256 != goal_hash or metadata.get("limits_sha256") != limits_hash:
                raise OrchestrationError("idempotency_key_conflict")
            return mission
        task = self.tasks.create_task(actor_id, workspace_id, goal, idempotency_key=key)
        mission_id = uuid4().hex
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="microseconds")
        expires = (now_dt + timedelta(seconds=lifetime_seconds)).isoformat(timespec="microseconds")
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
                active = conn.execute("SELECT COUNT(*) FROM missions WHERE workspace_id=? AND principal_id=? "
                                       "AND state NOT IN ('completed','cancelled','expired') AND archived_at IS NULL "
                                       "AND expires_at>?", (workspace_id, actor_id, now)).fetchone()[0]
                if active >= MAX_MISSIONS_PER_WORKSPACE:
                    raise OrchestrationError("mission_active_limit")
                conn.execute("INSERT INTO missions(id,task_id,workspace_id,principal_id,state,max_steps,max_requests,"
                             "max_retries,max_duration_seconds,expires_at,created_at,updated_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (mission_id, task.id, workspace_id, actor_id, "draft", max_steps, max_requests,
                              max_retries, max_duration_seconds, expires, now, now))
                mission = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone())
                self._event(conn, mission, actor_id, "mission_created", key,
                            metadata={"task_id": task.id, "goal_sha256": goal_hash,
                                      "limits_sha256": limits_hash})
                self.db._commit(conn)
            except sqlite3.IntegrityError:
                self.db._rollback(conn)
                with self.db._connection() as retry_conn:
                    duplicate = retry_conn.execute("SELECT m.id,e.metadata_json FROM missions m "
                        "JOIN mission_events e ON e.mission_id=m.id WHERE e.workspace_id=? AND e.actor_id=? "
                        "AND e.idempotency_key=? AND e.event_type='mission_created'",
                        (workspace_id, actor_id, key)).fetchone()
                if duplicate is None:
                    raise OrchestrationError("mission_conflict") from None
                try:
                    duplicate_meta = json.loads(duplicate["metadata_json"])
                except (ValueError, TypeError):
                    raise OrchestrationError("mission_evidence_corrupt") from None
                if (duplicate_meta.get("goal_sha256") != goal_hash
                        or duplicate_meta.get("limits_sha256") != limits_hash):
                    raise OrchestrationError("idempotency_key_conflict") from None
                return self.get(actor_id, duplicate["id"])
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission_id)

    def propose_fake_plan(self, actor_id: str, mission_id: str,
                          planner: DeterministicFakePlanner, *, idempotency_key: str):
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        if type(planner) is not DeterministicFakePlanner:
            raise OrchestrationError("model_planning_disabled")
        if mission.state not in {"draft", "awaiting_authorization", "blocked"}:
            raise OrchestrationError("mission_plan_not_editable")
        plan = self.tasks.propose_plan(actor_id, mission.task_id, planner,
                                       idempotency_key=_idempotency(idempotency_key))
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                conn.execute("UPDATE missions SET state='awaiting_authorization',plan_revision=?,"
                             "authorized_revision=NULL,authorization_sha256=NULL,updated_at=? WHERE id=?",
                             (plan.revision, _now(), mission.id))
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, "plan_linked", _idempotency(idempotency_key),
                            revision=plan.revision, digest=plan.plan_sha256,
                            metadata={"subtask_count": str(len(plan.subtasks))})
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return plan

    def propose_plan(self, actor_id: str, mission_id: str,
                     planner: MissionPlanningService, *, idempotency_key: str):
        """Create a reviewable plan; model planning never grants execution authority."""
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if type(planner) is not MissionPlanningService:
            raise OrchestrationError("mission_planner_unavailable")
        task = self.tasks.get_task(actor_id, mission.task_id)
        request = TaskPlanningRequest(
            task.id, task.workspace_id, task.repository_id, task.repository_commit, task.goal,
            task.requested_capabilities, tuple(task.capability_manifest.get("granted", [])),
        )
        request_digest = _digest(_canonical_json({
            "mission_id": mission.id, "task_id": task.id, "workspace_id": task.workspace_id,
            "principal_id": mission.principal_id, "goal_sha256": _digest(task.goal),
            "repository_id": task.repository_id, "repository_commit": task.repository_commit,
            "requested_capabilities": list(task.requested_capabilities), "model": planner.model,
            "provider_type": (type(planner.provider).__module__ + "." + type(planner.provider).__qualname__)
                if planner.provider is not None else None,
            "provider_timeout": getattr(planner.provider, "timeout", None),
            "provider_endpoint_sha256": _digest(str(getattr(planner.provider, "_endpoint", ""))),
            "config": planner.config.__dict__,
        }))
        owner = uuid4().hex
        lease_until = (datetime.now(timezone.utc) + timedelta(
            seconds=planner.config.planning_timeout_seconds + 15)).isoformat(timespec="microseconds")
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                row = conn.execute("SELECT * FROM mission_planning_reservations WHERE workspace_id=? AND "
                                   "principal_id=? AND mission_id=? AND idempotency_key=?",
                                   (mission.workspace_id, mission.principal_id, mission.id, key)).fetchone()
                if row is not None:
                    if row["request_sha256"] != request_digest:
                        raise OrchestrationError("idempotency_key_conflict")
                    if row["state"] == "completed":
                        self.db._commit(conn)
                        replay = self.tasks.get_plan(actor_id, mission.task_id, int(row["result_revision"]))
                        if replay.plan_sha256 != row["result_sha256"]:
                            raise OrchestrationError("mission_evidence_corrupt")
                        return replay
                    if row["state"] == "in_progress":
                        if datetime.fromisoformat(row["lease_expires_at"]) > datetime.now(timezone.utc):
                            raise OrchestrationError("mission_plan_in_progress")
                        conn.execute("UPDATE mission_planning_reservations SET state='interrupted',owner_token=NULL,"
                                     "lease_expires_at=NULL,updated_at=? WHERE workspace_id=? AND principal_id=? "
                                     "AND mission_id=? AND idempotency_key=?",
                                     (_now(), mission.workspace_id, mission.principal_id, mission.id, key))
                        self.db._commit(conn)
                    raise OrchestrationError("mission_plan_interrupted")
                old_event = conn.execute("SELECT mission_id,event_type,plan_revision FROM mission_events "
                                         "WHERE workspace_id=? AND actor_id=? AND idempotency_key=?",
                                         (mission.workspace_id, actor_id, key)).fetchone()
                if old_event is not None:
                    if old_event["mission_id"] == mission.id and old_event["event_type"] == "plan_linked":
                        self.db._commit(conn)
                        return self.tasks.get_plan(actor_id, mission.task_id, int(old_event["plan_revision"]))
                    raise OrchestrationError("idempotency_key_conflict")
                if mission.state not in {"draft", "awaiting_authorization", "blocked"}:
                    raise OrchestrationError("mission_plan_not_editable")
                task_event = conn.execute("SELECT task_id,event_type FROM orchestration_task_events "
                                          "WHERE workspace_id=? AND actor_id=? AND idempotency_key=?",
                                          (task.workspace_id, actor_id, key)).fetchone()
                if task_event is not None:
                    raise OrchestrationError("idempotency_key_conflict")
                conn.execute("INSERT INTO mission_planning_reservations(workspace_id,principal_id,mission_id,"
                             "idempotency_key,request_sha256,state,owner_token,lease_expires_at,created_at,updated_at) "
                             "VALUES(?,?,?,?,?,'in_progress',?,?,?,?)",
                             (mission.workspace_id, mission.principal_id, mission.id, key, request_digest,
                              owner, lease_until, _now(), _now()))
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise

        def reserve_attempt():
            with self.db._connection() as conn:
                self.db._begin(conn)
                try:
                    current = conn.execute("SELECT state,owner_token,attempts_used FROM mission_planning_reservations "
                                           "WHERE workspace_id=? AND principal_id=? AND mission_id=? AND idempotency_key=?",
                                           (mission.workspace_id, mission.principal_id, mission.id, key)).fetchone()
                    if current is None or current["state"] != "in_progress" or current["owner_token"] != owner:
                        raise OrchestrationError("mission_plan_owner_stale")
                    if current["attempts_used"] >= planner.config.max_attempts:
                        raise OrchestrationError("mission_plan_attempt_budget_exhausted")
                    cursor = conn.execute("UPDATE mission_planning_reservations SET attempts_used=attempts_used+1,updated_at=? "
                                          "WHERE workspace_id=? AND principal_id=? AND mission_id=? AND idempotency_key=? "
                                          "AND state='in_progress' AND owner_token=?",
                                          (_now(), mission.workspace_id, mission.principal_id, mission.id, key, owner))
                    if cursor.rowcount != 1:
                        raise OrchestrationError("mission_plan_owner_stale")
                    self.db._commit(conn)
                except Exception:
                    self.db._rollback(conn)
                    raise

        def interrupt_owned_reservation():
            with self.db._connection() as conn:
                conn.execute("UPDATE mission_planning_reservations SET state='interrupted',owner_token=NULL,"
                             "lease_expires_at=NULL,updated_at=? WHERE workspace_id=? AND principal_id=? "
                             "AND mission_id=? AND idempotency_key=? AND state='in_progress' AND owner_token=?",
                             (_now(), mission.workspace_id, mission.principal_id, mission.id, key, owner))
                conn.commit()

        try:
            result: MissionPlanResult = planner.plan(actor_id, request, before_attempt=reserve_attempt)
            if type(result) is not MissionPlanResult:
                raise OrchestrationError("mission_plan_invalid")
        except Exception:
            interrupt_owned_reservation()
            raise
        provenance = result.provenance()
        metadata = {name: ("" if value is None else str(value))
                    for name, value in provenance.items()}
        metadata["subtask_count"] = str(len(result.proposal.subtasks))
        metadata["provenance_sha256"] = _digest(_canonical_json(metadata))

        def commit_link(conn, task_row, revision, digest):
            Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
            current = conn.execute("SELECT state,owner_token FROM mission_planning_reservations WHERE "
                                   "workspace_id=? AND principal_id=? AND mission_id=? AND idempotency_key=?",
                                   (mission.workspace_id, mission.principal_id, mission.id, key)).fetchone()
            if current is None or current["state"] != "in_progress" or current["owner_token"] != owner:
                raise OrchestrationError("mission_plan_owner_stale")
            conn.execute("UPDATE missions SET state='awaiting_authorization',plan_revision=?,"
                         "authorized_revision=NULL,authorization_sha256=NULL,updated_at=? WHERE id=?",
                         (revision, _now(), mission.id))
            updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
            self._event(conn, updated, actor_id, "plan_linked", key,
                        revision=revision, digest=digest, metadata=metadata)
            cursor = conn.execute("UPDATE mission_planning_reservations SET state='completed',owner_token=NULL,"
                         "lease_expires_at=NULL,result_revision=?,result_sha256=?,updated_at=? WHERE workspace_id=? "
                         "AND principal_id=? AND mission_id=? AND idempotency_key=? AND owner_token=?",
                         (revision, digest, _now(), mission.workspace_id, mission.principal_id, mission.id, key, owner))
            if cursor.rowcount != 1:
                raise OrchestrationError("mission_plan_owner_stale")

        try:
            return self.tasks.propose_plan(
                actor_id, mission.task_id, DeterministicFakePlanner(result.proposal),
                idempotency_key=key, _transaction_hook=commit_link,
            )
        except Exception:
            interrupt_owned_reservation()
            raise

    def authorize(self, actor_id: str, mission_id: str, revision: int,
                  expected_plan_sha256: str, *, idempotency_key: str,
                  capabilities: tuple[str, ...] = ("simulate",)) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if tuple(capabilities) != ("simulate",):
            raise OrchestrationError("mission_capability_unavailable")
        with self.db._connection() as conn:
            replay = conn.execute("SELECT event_type,snapshot_sha256 FROM mission_events "
                                  "WHERE workspace_id=? AND actor_id=? AND idempotency_key=?",
                                  (mission.workspace_id, actor_id, key)).fetchone()
        if replay is not None:
            if replay["event_type"] != "mission_authorized" or mission.authorization_sha256 != replay["snapshot_sha256"]:
                raise OrchestrationError("idempotency_key_conflict")
            return mission
        if mission.state != "awaiting_authorization" or revision != mission.plan_revision:
            raise OrchestrationError("mission_plan_not_authorizable")
        plan = self.tasks.get_plan(actor_id, mission.task_id, revision)
        if plan.state not in {"awaiting_approval", "approved"} or plan.plan_sha256 != expected_plan_sha256:
            raise OrchestrationError("mission_plan_snapshot_mismatch")
        budget = {"max_steps": mission.max_steps, "max_requests": mission.max_requests,
                  "max_retries": mission.max_retries,
                  "max_duration_seconds": mission.max_duration_seconds}
        budget_hash = _digest(_canonical_json(budget))
        authorization = _digest(_canonical_json({"mission_id": mission.id,
            "workspace_id": mission.workspace_id, "principal_id": mission.principal_id,
            "revision": revision, "plan_sha256": plan.plan_sha256,
            "budget_sha256": budget_hash, "capabilities": ["simulate"]}))
        # The task approval is a distinct receipt and remains subject to its existing policy.
        task_key = hashlib.sha256(("mission-task-approval:" + key).encode()).hexdigest()
        if plan.state == "awaiting_approval":
            self.tasks.approve_plan(actor_id, mission.task_id, revision, plan.plan_sha256,
                                    idempotency_key=task_key)
        now = _now()
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                current = conn.execute("SELECT state,plan_revision FROM missions WHERE id=?", (mission.id,)).fetchone()
                if current is None or current["state"] != "awaiting_authorization" or current["plan_revision"] != revision:
                    raise OrchestrationError("mission_changed")
                auth_id = uuid4().hex
                conn.execute("INSERT INTO mission_authorizations(id,mission_id,workspace_id,principal_id,"
                             "plan_revision,plan_sha256,budget_sha256,capability_set_json,authorization_sha256,"
                             "approved_by,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (auth_id, mission.id, mission.workspace_id, mission.principal_id, revision,
                              plan.plan_sha256, budget_hash, '["simulate"]', authorization,
                              actor_id, key, now))
                conn.execute("UPDATE missions SET state='authorized',authorized_revision=?,"
                             "authorization_sha256=?,updated_at=? WHERE id=?",
                             (revision, authorization, now, mission.id))
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, "mission_authorized", key, revision=revision,
                            digest=authorization, metadata={"plan_sha256": plan.plan_sha256,
                                                            "budget_sha256": budget_hash})
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission.id)

    def reject(self, actor_id: str, mission_id: str, revision: int,
               expected_plan_sha256: str, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if mission.state != "awaiting_authorization" or revision != mission.plan_revision:
            raise OrchestrationError("mission_plan_not_rejectable")
        plan = self.tasks.get_plan(actor_id, mission.task_id, revision)
        if plan.state != "awaiting_approval" or plan.plan_sha256 != expected_plan_sha256:
            raise OrchestrationError("mission_plan_snapshot_mismatch")
        task_key = hashlib.sha256(("mission-task-rejection:" + key).encode()).hexdigest()
        self.tasks.reject_plan(actor_id, mission.task_id, revision, plan.plan_sha256,
                               idempotency_key=task_key)
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                conn.execute("UPDATE missions SET state='draft',authorized_revision=NULL,"
                             "authorization_sha256=NULL,updated_at=? WHERE id=? AND plan_revision=?",
                             (_now(), mission.id, revision))
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, "mission_rejected", key,
                            revision=revision, digest=plan.plan_sha256)
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission.id)

    def _assert_authorized(self, actor_id: str, mission: Mission) -> None:
        try:
            expires = datetime.fromisoformat(mission.expires_at)
            if expires.tzinfo is None:
                raise ValueError
            started = datetime.fromisoformat(mission.started_at) if mission.started_at else None
            if started is not None and started.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            raise OrchestrationError("mission_evidence_corrupt") from None
        if mission.archived_at or expires <= datetime.now(timezone.utc):
            raise OrchestrationError("mission_expired_or_archived")
        if mission.authorized_revision != mission.plan_revision or not mission.authorization_sha256:
            raise OrchestrationError("mission_authorization_required")
        plan = self.tasks.get_plan(actor_id, mission.task_id, mission.plan_revision)
        if plan.state != "approved":
            raise OrchestrationError("mission_task_plan_not_approved")
        budget = {"max_steps": mission.max_steps, "max_requests": mission.max_requests,
                  "max_retries": mission.max_retries,
                  "max_duration_seconds": mission.max_duration_seconds}
        budget_hash = _digest(_canonical_json(budget))
        expected = _digest(_canonical_json({"mission_id": mission.id,
            "workspace_id": mission.workspace_id, "principal_id": mission.principal_id,
            "revision": mission.plan_revision, "plan_sha256": plan.plan_sha256,
            "budget_sha256": budget_hash, "capabilities": ["simulate"]}))
        if expected != mission.authorization_sha256:
            raise OrchestrationError("mission_authorization_stale")
        with self.db._connection() as conn:
            receipt = conn.execute("SELECT plan_sha256,budget_sha256,capability_set_json,authorization_sha256 "
                "FROM mission_authorizations WHERE mission_id=? AND plan_revision=? AND authorization_sha256=?",
                (mission.id, mission.plan_revision, mission.authorization_sha256)).fetchone()
            totals = conn.execute("SELECT COALESCE(SUM(request_delta),0),COALESCE(SUM(step_delta),0),"
                "COALESCE(SUM(retry_delta),0) FROM mission_budget_ledger WHERE mission_id=?",
                (mission.id,)).fetchone()
        if (receipt is None or receipt["plan_sha256"] != plan.plan_sha256
                or receipt["budget_sha256"] != budget_hash
                or receipt["capability_set_json"] != '["simulate"]'
                or totals[0] != mission.requests_used or totals[1] != mission.steps_used
                or totals[2] != mission.retries_used):
            raise OrchestrationError("mission_evidence_corrupt")
        if started:
            if (datetime.now(timezone.utc) - started).total_seconds() >= mission.max_duration_seconds:
                raise OrchestrationError("mission_time_budget_exhausted")

    def simulate_next(self, actor_id: str, mission_id: str,
                      worker: DeterministicFakeWorker, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        if type(worker) is not DeterministicFakeWorker:
            raise OrchestrationError("real_worker_execution_disabled")
        self._assert_authorized(actor_id, mission)
        if mission.state not in {"authorized", "running"}:
            raise OrchestrationError("mission_not_runnable")
        self.verify_checkpoints(actor_id, mission.id)
        key = _idempotency(idempotency_key)
        with self.db._connection() as conn:
            prior = conn.execute("SELECT outcome FROM mission_budget_ledger WHERE mission_id=? AND operation_key=? "
                                 "ORDER BY id DESC LIMIT 1", (mission.id, key)).fetchone()
            reused = conn.execute("SELECT mission_id,event_type FROM mission_events WHERE workspace_id=? "
                                  "AND actor_id=? AND idempotency_key=?",
                                  (mission.workspace_id, actor_id, key)).fetchone()
        if reused is not None and (reused["mission_id"] != mission.id or reused["event_type"] not in
                                   {"mission_simulation_step", "mission_completed"}):
            raise OrchestrationError("idempotency_key_conflict")
        if prior is not None:
            if prior["outcome"] == "consumed":
                return self.get(actor_id, mission.id)
            if prior["outcome"] == "reserved":
                raise OrchestrationError("mission_operation_in_progress")
            raise OrchestrationError("mission_request_already_recorded")
        if mission.steps_used >= mission.max_steps or mission.requests_used >= mission.max_requests:
            raise OrchestrationError("mission_budget_exhausted")
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                same_key = conn.execute(
                    "SELECT outcome FROM mission_budget_ledger WHERE mission_id=? AND operation_key=? "
                    "ORDER BY id DESC LIMIT 1", (mission.id, key),
                ).fetchone()
                if same_key is not None:
                    if same_key["outcome"] == "consumed":
                        self.db._commit(conn)
                        return self.get(actor_id, mission.id)
                    if same_key["outcome"] == "reserved":
                        raise OrchestrationError("mission_operation_in_progress")
                    raise OrchestrationError("mission_request_already_recorded")
                active_operation = conn.execute(
                    "SELECT l.operation_key FROM mission_budget_ledger l WHERE l.mission_id=? "
                    "AND l.outcome='reserved' AND NOT EXISTS (SELECT 1 FROM mission_budget_ledger done "
                    "WHERE done.mission_id=l.mission_id AND done.operation_key=l.operation_key "
                    "AND done.outcome IN ('consumed','failed')) LIMIT 1", (mission.id,),
                ).fetchone()
                if active_operation is not None:
                    raise OrchestrationError("mission_operation_in_progress")
                update = conn.execute("UPDATE missions SET state='running',started_at=COALESCE(started_at,?),"
                             "steps_used=steps_used+1,requests_used=requests_used+1,updated_at=? "
                             "WHERE id=? AND state IN ('authorized','running') AND steps_used=? "
                             "AND requests_used=? AND retries_used=? AND steps_used<max_steps "
                             "AND requests_used<max_requests",
                             (_now(), _now(), mission.id, mission.steps_used,
                              mission.requests_used, mission.retries_used))
                if update.rowcount != 1:
                    raise OrchestrationError("mission_changed")
                conn.execute("INSERT INTO mission_budget_ledger(mission_id,workspace_id,principal_id,operation_key,"
                             "request_delta,step_delta,retry_delta,outcome,created_at) VALUES(?,?,?,?,1,1,0,'reserved',?)",
                             (mission.id, mission.workspace_id, mission.principal_id, key, _now()))
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise

        def commit_progress(connection, task, revision, plan_sha256):
            Database._require_access(connection, actor_id, mission.workspace_id, Permission.WRITE)
            Database._require_access(connection, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
            current = connection.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone()
            if current is None:
                raise OrchestrationError("mission_changed")
            try:
                current_expiry = datetime.fromisoformat(current["expires_at"])
                current_started = (datetime.fromisoformat(current["started_at"])
                                   if current["started_at"] else None)
                if (current_expiry.tzinfo is None
                        or (current_started is not None and current_started.tzinfo is None)):
                    raise ValueError
            except (TypeError, ValueError):
                raise OrchestrationError("mission_evidence_corrupt") from None
            commit_time = datetime.now(timezone.utc)
            if current_expiry <= commit_time:
                raise OrchestrationError("mission_expired_or_archived")
            if (current_started is not None
                    and (commit_time - current_started).total_seconds() >= current["max_duration_seconds"]):
                raise OrchestrationError("mission_time_budget_exhausted")
            if (current["state"] != "running"
                    or current["authorized_revision"] != revision
                    or current["plan_revision"] != revision
                    or current["authorization_sha256"] != mission.authorization_sha256
                    or current["steps_used"] != mission.steps_used + 1
                    or current["requests_used"] != mission.requests_used + 1
                    or current["retries_used"] != mission.retries_used):
                raise OrchestrationError("mission_changed")
            reserved = connection.execute(
                "SELECT 1 FROM mission_budget_ledger WHERE mission_id=? AND operation_key=? "
                "AND outcome='reserved'", (mission.id, key),
            ).fetchone()
            if reserved is None:
                raise OrchestrationError("mission_operation_stale")
            progress_rows = connection.execute(
                "SELECT subtask_id,state,progress_percent,result_summary FROM orchestration_subtasks "
                "WHERE task_id=? AND revision=? ORDER BY ordinal", (mission.task_id, revision),
            ).fetchall()
            snapshot = []
            for row in progress_rows:
                if (row["state"] not in {"pending", "blocked", "simulating", "simulated_completed",
                                        "simulated_failed", "cancelled"}
                        or type(row["progress_percent"]) is not int
                        or not 0 <= row["progress_percent"] <= 100):
                    raise OrchestrationError("mission_task_state_inconsistent")
                snapshot.append({"id": row["subtask_id"], "state": row["state"],
                                 "progress": row["progress_percent"],
                                 "summary_sha256": _digest(row["result_summary"] or "")})
            encoded = _canonical_json(snapshot)
            checkpoint_hash = _digest(encoded)
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM mission_checkpoints WHERE mission_id=?",
                (mission.id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO mission_checkpoints(mission_id,workspace_id,principal_id,sequence,"
                "plan_revision,progress_sha256,progress_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (mission.id, mission.workspace_id, mission.principal_id, sequence, revision,
                 checkpoint_hash, encoded, _now()),
            )
            state = "completed" if task.state.value == "simulation_completed" else "running"
            connection.execute(
                "UPDATE missions SET state=?,updated_at=? WHERE id=? AND state='running' "
                "AND steps_used=? AND requests_used=?",
                (state, _now(), mission.id, mission.steps_used + 1,
                 mission.requests_used + 1),
            )
            connection.execute(
                "INSERT INTO mission_budget_ledger(mission_id,workspace_id,principal_id,operation_key,"
                "request_delta,step_delta,retry_delta,outcome,created_at) "
                "VALUES(?,?,?,?,0,0,0,'consumed',?)",
                (mission.id, mission.workspace_id, mission.principal_id, key, _now()),
            )
            updated = self._row(connection.execute(
                "SELECT * FROM missions WHERE id=?", (mission.id,),
            ).fetchone())
            self._event(connection, updated, actor_id,
                        "mission_completed" if state == "completed" else "mission_simulation_step",
                        key, revision=revision, digest=checkpoint_hash,
                        metadata={"step_count": str(updated.steps_used),
                                  "checkpoint": str(sequence)})

        try:
            task = self.tasks.simulate_next_step(actor_id, mission.task_id, worker,
                idempotency_key=hashlib.sha256(("mission-sim:" + key).encode()).hexdigest(),
                _transaction_hook=commit_progress)
            if task.state.value == "blocked":
                raise OrchestrationError("mission_task_capability_revoked")
        except Exception as operation_error:
            # The task step and mission checkpoint/budget commit together. If
            # the transaction failed, quarantine both records in one second
            # transaction when authorization remains current.
            with self.db._connection() as conn:
                self.db._begin(conn)
                try:
                    Database._require_access(conn, actor_id, mission.workspace_id, Permission.WRITE)
                    Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                    current = conn.execute("SELECT state,steps_used,requests_used,retries_used,max_retries "
                                           "FROM missions WHERE id=?", (mission.id,)).fetchone()
                    pending = conn.execute(
                        "SELECT 1 FROM mission_budget_ledger l WHERE l.mission_id=? AND l.operation_key=? "
                        "AND l.outcome='reserved' AND NOT EXISTS (SELECT 1 FROM mission_budget_ledger done "
                        "WHERE done.mission_id=l.mission_id AND done.operation_key=l.operation_key "
                        "AND done.outcome IN ('consumed','failed'))", (mission.id, key),
                    ).fetchone()
                    if (current is not None and pending is not None and current["state"] == "running"
                            and current["steps_used"] == mission.steps_used + 1
                            and current["requests_used"] == mission.requests_used + 1):
                        retry = int(current["retries_used"] < current["max_retries"])
                        task_row = conn.execute(
                            "SELECT * FROM orchestration_tasks WHERE id=? AND state IN ('queued','simulating')",
                            (mission.task_id,),
                        ).fetchone()
                        if task_row is not None:
                            task = self.tasks._task(task_row)
                            blocked = conn.execute(
                                "UPDATE orchestration_tasks SET state='blocked',updated_at=? WHERE id=? "
                                "AND state IN ('queued','simulating')", (_now(), mission.task_id),
                            )
                            if blocked.rowcount:
                                updated_task = self.tasks._task(conn.execute(
                                    "SELECT * FROM orchestration_tasks WHERE id=?", (mission.task_id,),
                                ).fetchone())
                                task_failure_key = hashlib.sha256(("mission-task-failure:" + key).encode()).hexdigest()
                                self.tasks._insert_event(conn, updated_task, actor_id, "task_blocked",
                                    task_failure_key, revision=task.current_plan_revision,
                                    snapshot_sha256=task.capability_manifest_sha256,
                                    metadata={"reason": "mission_operation_failed"})
                        conn.execute("UPDATE missions SET state='blocked',retries_used=retries_used+?,updated_at=? "
                                     "WHERE id=? AND state='running' AND steps_used=? AND requests_used=?",
                                     (retry, _now(), mission.id, mission.steps_used + 1,
                                      mission.requests_used + 1))
                        conn.execute("INSERT INTO mission_budget_ledger(mission_id,workspace_id,principal_id,"
                                     "operation_key,request_delta,step_delta,retry_delta,outcome,created_at) "
                                     "VALUES(?,?,?, ?,0,0,?,'failed',?)",
                                     (mission.id, mission.workspace_id, mission.principal_id, key, retry, _now()))
                        self.db._commit(conn)
                    else:
                        self.db._rollback(conn)
                except Exception:
                    self.db._rollback(conn)
            raise operation_error
        return self.get(actor_id, mission.id)

    def pause(self, actor_id: str, mission_id: str, *, idempotency_key: str) -> Mission:
        return self._transition(actor_id, mission_id, "mission_paused", "paused", {"authorized", "running"}, idempotency_key)

    def cancel(self, actor_id: str, mission_id: str, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if mission.state == "cancelled":
            return mission
        self.tasks.cancel_task(actor_id, mission.task_id,
                               idempotency_key=hashlib.sha256(("mission-cancel:" + key).encode()).hexdigest())
        return self._transition(actor_id, mission_id, "mission_cancelled", "cancelled",
                                {mission.state}, key)

    def _transition(self, actor_id: str, mission_id: str, event: str, state: str,
                    allowed: set[str], idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if mission.state == state:
            return mission
        if mission.state not in allowed:
            raise OrchestrationError("mission_invalid_transition")
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                changed = conn.execute("UPDATE missions SET state=?,updated_at=? WHERE id=? AND state=?",
                                       (state, _now(), mission.id, mission.state))
                if changed.rowcount != 1:
                    raise OrchestrationError("mission_changed")
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, event, key, revision=mission.plan_revision)
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission.id)

    def resume(self, actor_id: str, mission_id: str, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        if mission.state not in {"paused", "interrupted", "blocked"}:
            raise OrchestrationError("mission_not_resumable")
        if mission.state == "blocked" and mission.retries_used >= mission.max_retries:
            raise OrchestrationError("mission_retry_budget_exhausted")
        self._assert_authorized(actor_id, mission)
        self.verify_checkpoints(actor_id, mission.id)
        task = self.tasks.get_task(actor_id, mission.task_id)
        if task.state.value in {"interrupted", "blocked"}:
            self.tasks.resume_simulation(actor_id, mission.task_id,
                idempotency_key=hashlib.sha256(("mission-task-resume:" + _idempotency(idempotency_key)).encode()).hexdigest())
        target = "running" if mission.steps_used else "authorized"
        return self._transition(actor_id, mission_id, "mission_resumed", target,
                                {mission.state}, idempotency_key)

    def recover_after_restart(self, actor_id: str, workspace_id: str) -> tuple[str, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        interrupted: list[str] = []
        now = _now()
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, workspace_id, Permission.READ)
                Database._require_access(conn, actor_id, workspace_id, Permission.WRITE)
                Database._require_access(conn, actor_id, workspace_id, Permission.MANAGE_WORKSPACE)
                # A reservation can outlive its provider call if the backend dies.
                # Fence every such owner at startup; lease expiry alone would
                # leave a live-looking reservation until another planner retries.
                conn.execute(
                    "UPDATE mission_planning_reservations SET state='interrupted',"
                    "owner_token=NULL,lease_expires_at=NULL,updated_at=? "
                    "WHERE workspace_id=? AND state='in_progress'",
                    (now, workspace_id),
                )
                active_tasks = conn.execute(
                    "SELECT * FROM orchestration_tasks WHERE workspace_id=? AND archived_at IS NULL "
                    "AND state IN ('queued','simulating') ORDER BY id", (workspace_id,),
                ).fetchall()
                for row in active_tasks:
                    prior_state = row["state"]
                    task = self.tasks._task(row)
                    cursor = conn.execute(
                        "UPDATE orchestration_tasks SET state='interrupted',updated_at=? "
                        "WHERE id=? AND state=? AND archived_at IS NULL",
                        (now, task.id, prior_state),
                    )
                    if cursor.rowcount != 1:
                        continue
                    updated_task = self.tasks._task(conn.execute(
                        "SELECT * FROM orchestration_tasks WHERE id=?", (task.id,),
                    ).fetchone())
                    event_key = hashlib.sha256(
                        f"m08d-restart-task:{task.id}:{task.updated_at}:{prior_state}".encode()
                    ).hexdigest()
                    self.tasks._insert_event(
                        conn, updated_task, actor_id, "task_interrupted", event_key,
                        revision=task.approved_plan_revision,
                        snapshot_sha256=task.capability_manifest_sha256,
                        metadata={"previous_state": prior_state, "auto_resume": "false"},
                    )

                missions = conn.execute(
                    "SELECT * FROM missions WHERE workspace_id=? AND archived_at IS NULL "
                    "AND state NOT IN ('completed','cancelled','expired') ORDER BY id", (workspace_id,),
                ).fetchall()
                for row in missions:
                    mission = self._row(row)
                    try:
                        expires = datetime.fromisoformat(mission.expires_at)
                        if expires.tzinfo is None:
                            raise ValueError
                    except (TypeError, ValueError):
                        raise OrchestrationError("mission_evidence_corrupt") from None
                    expired = expires <= datetime.now(timezone.utc)
                    target = "expired" if expired else "interrupted"
                    should_transition = expired or mission.state in {"authorized", "running"}
                    if should_transition:
                        cursor = conn.execute(
                            "UPDATE missions SET state=?,updated_at=? WHERE id=? "
                            "AND workspace_id=? AND principal_id=? AND state=? AND archived_at IS NULL",
                            (target, now, mission.id, workspace_id, mission.principal_id, mission.state),
                        )
                        if cursor.rowcount != 1:
                            continue
                        updated = self._row(conn.execute(
                            "SELECT * FROM missions WHERE id=?", (mission.id,),
                        ).fetchone())
                        event_key = hashlib.sha256(
                            f"m08d-restart-mission:{mission.id}:{mission.updated_at}:{mission.state}:{target}".encode()
                        ).hexdigest()
                        self._event(conn, updated, actor_id, "mission_interrupted", event_key,
                                    revision=mission.plan_revision,
                                    metadata={"previous_state": mission.state,
                                              "auto_resume": "false",
                                              "reason": "deadline_expired" if expired else "restart"})
                        interrupted.append(mission.id)
                    reservations = conn.execute(
                        "SELECT l.operation_key FROM mission_budget_ledger l WHERE l.mission_id=? "
                        "AND l.outcome='reserved' AND NOT EXISTS (SELECT 1 FROM mission_budget_ledger done "
                        "WHERE done.mission_id=l.mission_id AND done.operation_key=l.operation_key "
                        "AND done.outcome IN ('consumed','failed')) ORDER BY l.id",
                        (mission.id,),
                    ).fetchall()
                    for reservation in reservations:
                        conn.execute(
                            "INSERT INTO mission_budget_ledger(mission_id,workspace_id,principal_id,operation_key,"
                            "request_delta,step_delta,retry_delta,outcome,created_at) "
                            "VALUES(?,?,?, ?,0,0,0,'failed',?)",
                            (mission.id, mission.workspace_id, mission.principal_id,
                             reservation["operation_key"], now),
                        )
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return tuple(interrupted)

    def events(self, actor_id: str, mission_id: str, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        mission = self._require(actor_id, mission_id)
        if type(limit) is not int or not 1 <= limit <= 250:
            raise ValueError("mission event limit must be between 1 and 250")
        with self.db._connection() as conn:
            rows = conn.execute("SELECT * FROM mission_events WHERE mission_id=? AND workspace_id=? "
                                "ORDER BY id DESC LIMIT ?", (mission.id, mission.workspace_id, limit)).fetchall()
        result = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"])
                if self._metadata(metadata) != row["metadata_json"]:
                    raise ValueError
                provenance_sha = metadata.get("provenance_sha256")
                if provenance_sha is not None:
                    evidence = dict(metadata)
                    del evidence["provenance_sha256"]
                    if provenance_sha != _digest(_canonical_json(evidence)):
                        raise ValueError
            except (TypeError, ValueError):
                raise OrchestrationError("mission_evidence_corrupt") from None
            result.append({"id": row["id"], "event_type": row["event_type"],
                           "actor_id": row["actor_id"], "plan_revision": row["plan_revision"],
                           "snapshot_sha256": row["snapshot_sha256"], "metadata": metadata,
                           "created_at": row["created_at"]})
        return tuple(result)

    def verify_checkpoints(self, actor_id: str, mission_id: str) -> bool:
        mission = self._require(actor_id, mission_id)
        with self.db._connection() as conn:
            rows = conn.execute("SELECT * FROM mission_checkpoints WHERE mission_id=? ORDER BY sequence",
                                (mission.id,)).fetchall()
        previous = 0
        for row in rows:
            if row["sequence"] != previous + 1 or _digest(row["progress_json"]) != row["progress_sha256"]:
                raise OrchestrationError("mission_checkpoint_corrupt")
            try:
                progress = json.loads(row["progress_json"])
                if not isinstance(progress, list) or len(progress) > MAX_MISSION_STEPS:
                    raise ValueError
            except (TypeError, ValueError):
                raise OrchestrationError("mission_checkpoint_corrupt") from None
            previous = row["sequence"]
        current = self.tasks.get_progress(actor_id, mission.task_id, mission.plan_revision) if mission.plan_revision else ()
        snapshot = [{"id": p.subtask_id, "state": p.state.value,
                     "progress": p.progress_percent,
                     "summary_sha256": _digest(p.result_summary or "")} for p in current]
        if rows:
            if _digest(_canonical_json(snapshot)) != rows[-1]["progress_sha256"]:
                raise OrchestrationError("mission_checkpoint_stale")
        elif any(p.progress_percent for p in current):
            raise OrchestrationError("mission_checkpoint_missing")
        return True

    def archive(self, actor_id: str, mission_id: str, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if mission.state not in {"completed", "cancelled", "expired"}:
            raise OrchestrationError("mission_active")
        self.tasks.archive_task(actor_id, mission.task_id,
                                idempotency_key=hashlib.sha256(("mission-archive:" + _idempotency(idempotency_key)).encode()).hexdigest())
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                conn.execute("UPDATE missions SET archived_at=COALESCE(archived_at,?),archived_by=COALESCE(archived_by,?),updated_at=? WHERE id=?",
                             (_now(), actor_id, _now(), mission.id))
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, "mission_archived", key,
                            revision=mission.plan_revision)
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission.id)

    def restore(self, actor_id: str, mission_id: str, *, idempotency_key: str) -> Mission:
        mission = self._require(actor_id, mission_id, write=True, manage=True)
        key = _idempotency(idempotency_key)
        if mission.archived_at is None:
            return mission
        self.tasks.restore_archived_task(actor_id, mission.task_id,
                                         idempotency_key=hashlib.sha256(("mission-restore:" + _idempotency(idempotency_key)).encode()).hexdigest())
        with self.db._connection() as conn:
            self.db._begin(conn)
            try:
                Database._require_access(conn, actor_id, mission.workspace_id, Permission.MANAGE_WORKSPACE)
                conn.execute("UPDATE missions SET archived_at=NULL,archived_by=NULL,updated_at=? WHERE id=?",
                             (_now(), mission.id))
                updated = self._row(conn.execute("SELECT * FROM missions WHERE id=?", (mission.id,)).fetchone())
                self._event(conn, updated, actor_id, "mission_restored", key,
                            revision=mission.plan_revision)
                self.db._commit(conn)
            except Exception:
                self.db._rollback(conn)
                raise
        return self.get(actor_id, mission.id)

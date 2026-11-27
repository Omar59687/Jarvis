"""Opt-in deterministic proactivity over existing mission/task state.

This module never calls a model, creates a mission, resumes work, or invokes a
worker. Scheduler ticks are synchronous and are intended to be called only by
the explicitly started local service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from .storage import AccessDenied, Database, Permission, _now, _nonempty


DEFAULT_CHECK_INTERVAL_SECONDS = 3600
MIN_CHECK_INTERVAL_SECONDS = 900
MAX_CHECK_INTERVAL_SECONDS = 86400
UNFINISHED_MISSION_SECONDS = 86400
DEFAULT_COOLDOWN_SECONDS = 86400
DEFAULT_SUGGESTION_LIFETIME_DAYS = 30
MAX_ACTIVE_SUGGESTIONS = 100
MAX_SCANNED_RECORDS_PER_SCOPE = 100
MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK = 25
DEFAULT_SCOPES_PER_TICK = 1
MAX_SCOPES_PER_TICK = 10
MAX_SCHEDULER_LEASE_SECONDS = 300
MAX_SNOOZE_SECONDS = 30 * 86400
_REASON_CODES = frozenset({
    "mission_aged", "mission_blocked", "mission_paused", "mission_interrupted",
    "task_blocked", "task_interrupted",
})
_MISSION_UNFINISHED = frozenset({
    "draft", "awaiting_authorization", "authorized", "running", "paused",
    "blocked", "interrupted",
})
_MISSION_STATES = frozenset({"paused", "blocked", "interrupted"})
_TASK_STATES = frozenset({"blocked", "interrupted"})
_EVENTS = frozenset({
    "suggestion_created", "suggestion_accepted", "suggestion_dismissed",
    "suggestion_snoozed", "suggestion_unsnoozed", "suggestion_retracted",
    "suggestion_expired",
})
_SHA256 = frozenset("0123456789abcdef")


class ProactivityError(AccessDenied):
    """Stable proactivity error that does not expose inaccessible scope data."""


@dataclass(frozen=True)
class ProactivityPreferences:
    workspace_id: str
    principal_id: str
    enabled: bool
    generation: int
    check_interval_seconds: int
    cooldown_seconds: int
    created_at: str | None
    updated_at: str | None
    can_enable: bool


@dataclass(frozen=True)
class Suggestion:
    id: str
    workspace_id: str
    principal_id: str
    suggestion_type: str
    reason_code: str
    source_kind: str
    source_task_id: str
    source_mission_id: str | None
    state: str
    created_at: str
    expires_at: str
    snoozed_until: str | None


@dataclass(frozen=True)
class _Candidate:
    suggestion_type: str
    reason_code: str
    source_kind: str
    task_id: str
    mission_id: str | None
    source_digest: str


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("invalid timestamp")
    return parsed.astimezone(timezone.utc)


def _digest(payload: object) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _row_suggestion(row: sqlite3.Row) -> Suggestion:
    try:
        state = row["state"]
        if state not in {"active", "accepted", "dismissed", "snoozed", "retracted", "expired"}:
            raise ValueError
        reason = row["reason_code"]
        if reason != "legacy" and reason not in _REASON_CODES:
            raise ValueError
        if row["suggestion_type"] not in {"mission_follow_up", "task_follow_up"}:
            raise ValueError
        if row["source_kind"] not in {"mission", "task"}:
            raise ValueError
        if ((row["source_kind"] == "mission") !=
                (row["suggestion_type"] == "mission_follow_up")):
            raise ValueError
        return Suggestion(*(row[name] for name in (
            "id", "workspace_id", "principal_id", "suggestion_type", "reason_code",
            "source_kind", "source_task_id", "source_mission_id", "state", "created_at",
            "expires_at", "snoozed_until",
        )))
    except (KeyError, TypeError, ValueError):
        raise ProactivityError("suggestion_evidence_corrupt") from None


def suggestion_payload(item: Suggestion) -> dict[str, str | None]:
    """Return only fixed codes, identity references, and lifecycle timestamps."""
    return {
        "id": item.id,
        "workspace_id": item.workspace_id,
        "suggestion_type": item.suggestion_type,
        "reason_code": item.reason_code,
        "source_kind": item.source_kind,
        "source_task_id": item.source_task_id,
        "source_mission_id": item.source_mission_id,
        "state": item.state,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "snoozed_until": item.snoozed_until,
    }


class ProactivityService:
    """Permission-checked preferences, evidence-bound inbox, and bounded scanner."""

    def __init__(self, database: Database, *, clock=None,
                 scopes_per_tick: int = DEFAULT_SCOPES_PER_TICK,
                 lease_seconds: int = 60):
        if type(scopes_per_tick) is not int or not 1 <= scopes_per_tick <= MAX_SCOPES_PER_TICK:
            raise ValueError("scopes_per_tick must be between 1 and 10")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= MAX_SCHEDULER_LEASE_SECONDS:
            raise ValueError("scheduler lease must be between 1 and 300 seconds")
        self.db = database
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.scopes_per_tick = scopes_per_tick
        self.lease_seconds = lease_seconds

    def _now(self) -> datetime:
        return _utc(self._clock())

    @staticmethod
    def _require_scope(connection: sqlite3.Connection, actor_id: str,
                       workspace_id: str, *, write: bool) -> None:
        Database._require_access(connection, actor_id, workspace_id, Permission.READ)
        if write:
            Database._require_access(connection, actor_id, workspace_id, Permission.WRITE)

    def get_preferences(self, actor_id: str, workspace_id: str) -> ProactivityPreferences:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        with self.db._connection() as connection:
            self._require_scope(connection, actor_id, workspace_id, write=False)
            row = connection.execute(
                "SELECT * FROM proactivity_preferences WHERE workspace_id=? AND principal_id=?",
                (workspace_id, actor_id),
            ).fetchone()
            can_enable = connection.execute(
                "SELECT 1 FROM workspace_permissions WHERE workspace_id=? AND principal_id=? "
                "AND permission=?", (workspace_id, actor_id, Permission.WRITE.value),
            ).fetchone() is not None
        if row is None:
            return ProactivityPreferences(
                workspace_id, actor_id, False, 0, DEFAULT_CHECK_INTERVAL_SECONDS,
                DEFAULT_COOLDOWN_SECONDS, None, None, can_enable,
            )
        try:
            enabled = bool(row["enabled"]) and can_enable
            if (type(row["generation"]) is not int or row["generation"] < 0
                    or type(row["check_interval_seconds"]) is not int
                    or not MIN_CHECK_INTERVAL_SECONDS <= row["check_interval_seconds"] <= MAX_CHECK_INTERVAL_SECONDS
                    or type(row["cooldown_seconds"]) is not int
                    or not 0 <= row["cooldown_seconds"] <= 30 * 86400):
                raise ValueError
            return ProactivityPreferences(
                workspace_id, actor_id, enabled, row["generation"],
                row["check_interval_seconds"], row["cooldown_seconds"],
                row["created_at"], row["updated_at"], can_enable,
            )
        except (TypeError, ValueError):
            raise ProactivityError("proactivity_preferences_corrupt") from None

    def set_preferences(self, actor_id: str, workspace_id: str, *, enabled: bool,
                        check_interval_seconds: int | None = None,
                        cooldown_seconds: int | None = None) -> ProactivityPreferences:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        if check_interval_seconds is not None and (
                type(check_interval_seconds) is not int
                or not MIN_CHECK_INTERVAL_SECONDS <= check_interval_seconds <= MAX_CHECK_INTERVAL_SECONDS):
            raise ValueError("check interval must be between 900 and 86400 seconds")
        if cooldown_seconds is not None and (
                type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 30 * 86400):
            raise ValueError("cooldown must be between 0 and 2592000 seconds")
        now_dt = self._now()
        now = _stamp(now_dt)
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                self._require_scope(connection, actor_id, workspace_id, write=enabled)
                existing = connection.execute(
                    "SELECT generation,created_at,check_interval_seconds,cooldown_seconds "
                    "FROM proactivity_preferences "
                    "WHERE workspace_id=? AND principal_id=?", (workspace_id, actor_id),
                ).fetchone()
                generation = (int(existing["generation"]) if existing else 0) + 1
                created_at = existing["created_at"] if existing else now
                interval = (check_interval_seconds if check_interval_seconds is not None else
                            int(existing["check_interval_seconds"]) if existing else
                            DEFAULT_CHECK_INTERVAL_SECONDS)
                cooldown = (cooldown_seconds if cooldown_seconds is not None else
                            int(existing["cooldown_seconds"]) if existing else
                            DEFAULT_COOLDOWN_SECONDS)
                connection.execute(
                    "INSERT INTO proactivity_preferences(workspace_id,principal_id,enabled,generation,"
                    "check_interval_seconds,cooldown_seconds,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(workspace_id,principal_id) DO UPDATE SET enabled=excluded.enabled,"
                    "generation=excluded.generation,check_interval_seconds=excluded.check_interval_seconds,"
                    "cooldown_seconds=excluded.cooldown_seconds,updated_at=excluded.updated_at",
                    (workspace_id, actor_id, int(enabled), generation, interval,
                     cooldown, created_at, now),
                )
                connection.execute(
                    "INSERT INTO proactivity_scheduler_claims(workspace_id,principal_id,generation,next_check_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(workspace_id,principal_id) DO UPDATE SET "
                    "generation=excluded.generation,claim_token=NULL,lease_expires_at=NULL,"
                    "next_check_at=excluded.next_check_at",
                    (workspace_id, actor_id, generation, now if enabled else None),
                )
                Database._insert_audit(
                    connection, workspace_id, actor_id, "proactivity.preferences_changed",
                    "proactivity_preference", actor_id,
                    {"enabled": str(enabled).lower(), "generation": str(generation),
                     "check_interval_seconds": str(interval),
                     "cooldown_seconds": str(cooldown)},
                )
                self.db._commit(connection)
            except Exception:
                self.db._rollback(connection)
                raise
        return self.get_preferences(actor_id, workspace_id)

    def stop(self, actor_id: str, workspace_id: str) -> ProactivityPreferences:
        """Fence all in-flight checks and persist disabled state."""
        return self.set_preferences(actor_id, workspace_id, enabled=False)

    def _source_row(self, connection: sqlite3.Connection, workspace_id: str,
                    principal_id: str, task_id: str, mission_id: str | None):
        row = connection.execute(
            "SELECT t.id,t.workspace_id,t.principal_id,t.state AS task_state,t.updated_at AS task_updated_at,"
            "t.current_plan_revision,t.repository_commit,t.archived_at AS task_archived_at,"
            "m.id AS mission_id,m.state AS mission_state,m.updated_at AS mission_updated_at,"
            "m.archived_at AS mission_archived_at,m.created_at AS mission_created_at "
            "FROM orchestration_tasks t LEFT JOIN missions m ON m.task_id=t.id "
            "AND m.workspace_id=t.workspace_id AND m.principal_id=t.principal_id "
            "WHERE t.id=? AND t.workspace_id=? AND t.principal_id=?",
            (task_id, workspace_id, principal_id),
        ).fetchone()
        if row is None or (mission_id is not None and row["mission_id"] != mission_id):
            return None
        if row["task_archived_at"] is not None or row["mission_archived_at"] is not None:
            return None
        return row

    @staticmethod
    def _source_digest(row: sqlite3.Row) -> str:
        return _digest({
            "task_id": row["id"], "workspace_id": row["workspace_id"],
            "principal_id": row["principal_id"], "task_state": row["task_state"],
            "task_updated_at": row["task_updated_at"],
            "current_plan_revision": row["current_plan_revision"],
            "repository_commit": row["repository_commit"],
            "mission_id": row["mission_id"], "mission_state": row["mission_state"],
            "mission_updated_at": row["mission_updated_at"],
        })

    @staticmethod
    def _event(connection: sqlite3.Connection, item: Suggestion, event_type: str,
               now: str, metadata: dict[str, str] | None = None) -> None:
        if event_type not in _EVENTS:
            raise ValueError("unsupported suggestion event")
        rendered = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        if len(rendered.encode("utf-8")) > 2048:
            raise ValueError("suggestion event metadata is too large")
        connection.execute(
            "INSERT INTO proactivity_suggestion_events(suggestion_id,workspace_id,principal_id,"
            "source_task_id,event_type,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (item.id, item.workspace_id, item.principal_id, item.source_task_id,
             event_type, rendered, now),
        )

    def _audit(self, connection: sqlite3.Connection, actor_id: str, item: Suggestion,
               event_type: str) -> None:
        Database._insert_audit(
            connection, item.workspace_id, actor_id, f"proactivity.{event_type}",
            "proactive_suggestion", item.id,
            {"suggestion_type": item.suggestion_type, "reason_code": item.reason_code,
             "state": item.state},
        )

    def _transition(self, connection: sqlite3.Connection, row: sqlite3.Row,
                    state: str, event: str, now: str,
                    *, snoozed_until: str | None = None) -> Suggestion:
        connection.execute(
            "UPDATE proactive_suggestions SET state=?,snoozed_until=? WHERE id=? "
            "AND workspace_id=? AND principal_id=?",
            (state, snoozed_until, row["id"], row["workspace_id"], row["principal_id"]),
        )
        updated = connection.execute(
            "SELECT * FROM proactive_suggestions WHERE id=? AND workspace_id=? AND principal_id=?",
            (row["id"], row["workspace_id"], row["principal_id"]),
        ).fetchone()
        item = _row_suggestion(updated)
        metadata = {"reason_code": item.reason_code}
        if snoozed_until is not None:
            metadata["snoozed_until"] = snoozed_until
        self._event(connection, item, event, now, metadata)
        self._audit(connection, item.principal_id, item, event.removeprefix("suggestion_"))
        return item

    def _reconcile(self, connection: sqlite3.Connection, actor_id: str,
                   workspace_id: str, now: datetime) -> None:
        now_stamp = _stamp(now)
        self._require_scope(connection, actor_id, workspace_id, write=True)
        rows = connection.execute(
            "SELECT * FROM proactive_suggestions WHERE workspace_id=? AND principal_id=? "
            "AND state IN ('active','snoozed') ORDER BY created_at,id LIMIT ?",
            (workspace_id, actor_id, MAX_ACTIVE_SUGGESTIONS),
        ).fetchall()
        for row in rows:
            item = _row_suggestion(row)
            try:
                expires = _parse_stamp(item.expires_at)
                snooze = _parse_stamp(item.snoozed_until) if item.snoozed_until else None
            except ValueError:
                raise ProactivityError("suggestion_evidence_corrupt") from None
            if expires <= now:
                self._transition(connection, row, "expired", "suggestion_expired", now_stamp)
                continue
            if item.state == "snoozed" and snooze is not None and snooze <= now:
                self._transition(connection, row, "active", "suggestion_unsnoozed", now_stamp)
                continue
            source = self._source_row(connection, workspace_id, actor_id,
                                      item.source_task_id, item.source_mission_id)
            if source is None or self._source_digest(source) != row["source_revision_sha256"]:
                self._transition(connection, row, "retracted", "suggestion_retracted", now_stamp)

    def list_suggestions(self, actor_id: str, workspace_id: str, *,
                         limit: int = MAX_ACTIVE_SUGGESTIONS) -> tuple[Suggestion, ...]:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        if type(limit) is not int or not 1 <= limit <= MAX_ACTIVE_SUGGESTIONS:
            raise ValueError("suggestion limit is outside its bound")
        now = self._now()
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                self._reconcile(connection, actor_id, workspace_id, now)
                rows = connection.execute(
                    "SELECT * FROM proactive_suggestions WHERE workspace_id=? AND principal_id=? "
                    "AND state IN ('active','snoozed') ORDER BY created_at DESC,id LIMIT ?",
                    (workspace_id, actor_id, limit),
                ).fetchall()
                result = tuple(_row_suggestion(row) for row in rows)
                self.db._commit(connection)
                return result
            except Exception:
                self.db._rollback(connection)
                raise

    def _load_actionable(self, connection: sqlite3.Connection, actor_id: str,
                         workspace_id: str, suggestion_id: str, now: datetime):
        self._require_scope(connection, actor_id, workspace_id, write=True)
        row = connection.execute(
            "SELECT * FROM proactive_suggestions WHERE id=? AND workspace_id=? AND principal_id=?",
            (suggestion_id, workspace_id, actor_id),
        ).fetchone()
        if row is None:
            raise ProactivityError("suggestion_unavailable")
        item = _row_suggestion(row)
        if item.state not in {"active", "snoozed"}:
            raise ProactivityError("suggestion_not_actionable")
        try:
            if _parse_stamp(item.expires_at) <= now:
                return self._transition(connection, row, "expired", "suggestion_expired", _stamp(now))
        except ValueError:
            raise ProactivityError("suggestion_evidence_corrupt") from None
        source = self._source_row(connection, workspace_id, actor_id,
                                  item.source_task_id, item.source_mission_id)
        if source is None or self._source_digest(source) != row["source_revision_sha256"]:
            return self._transition(connection, row, "retracted", "suggestion_retracted", _stamp(now))
        return item

    def act(self, actor_id: str, workspace_id: str, suggestion_id: str,
            action: str, *, snooze_seconds: int | None = None) -> Suggestion:
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        suggestion_id = _nonempty(suggestion_id, "suggestion ID")
        now = self._now()
        if action not in {"accept", "dismiss", "snooze"}:
            raise ValueError("unsupported suggestion action")
        if action == "snooze":
            if type(snooze_seconds) is not int or not 60 <= snooze_seconds <= MAX_SNOOZE_SECONDS:
                raise ValueError("snooze must be between 60 seconds and 30 days")
        elif snooze_seconds is not None:
            raise ValueError("snooze duration is only valid for snooze")
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                item = self._load_actionable(connection, actor_id, workspace_id,
                                             suggestion_id, now)
                if item.state not in {"active", "snoozed"}:
                    self.db._commit(connection)
                    return item
                target, event = {
                    "accept": ("accepted", "suggestion_accepted"),
                    "dismiss": ("dismissed", "suggestion_dismissed"),
                    "snooze": ("snoozed", "suggestion_snoozed"),
                }[action]
                snoozed_until = None
                if action == "snooze":
                    snoozed = now + timedelta(seconds=snooze_seconds)
                    if snoozed >= _parse_stamp(item.expires_at):
                        raise ValueError("snooze must end before suggestion expiration")
                    snoozed_until = _stamp(snoozed)
                row = connection.execute(
                    "SELECT * FROM proactive_suggestions WHERE id=? AND workspace_id=? AND principal_id=?",
                    (item.id, workspace_id, actor_id),
                ).fetchone()
                updated = self._transition(connection, row, target, event, _stamp(now),
                                           snoozed_until=snoozed_until)
                self.db._commit(connection)
                return updated
            except Exception:
                self.db._rollback(connection)
                raise

    def _eligible_scopes(self, now: datetime) -> tuple[tuple[str, str], ...]:
        now_stamp = _stamp(now)
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT p.workspace_id,p.principal_id FROM proactivity_preferences p "
                "JOIN workspace_permissions r ON r.workspace_id=p.workspace_id "
                "AND r.principal_id=p.principal_id AND r.permission='read' "
                "JOIN workspace_permissions w ON w.workspace_id=p.workspace_id "
                "AND w.principal_id=p.principal_id AND w.permission='write' "
                "LEFT JOIN proactivity_scheduler_claims c ON c.workspace_id=p.workspace_id "
                "AND c.principal_id=p.principal_id WHERE p.enabled=1 AND "
                "(c.generation IS NULL OR c.generation<>p.generation OR "
                "(c.claim_token IS NULL AND (c.next_check_at IS NULL OR c.next_check_at<=?)) OR "
                "(c.lease_expires_at IS NOT NULL AND c.lease_expires_at<=?)) "
                "ORDER BY COALESCE(c.next_check_at,'') ASC,p.workspace_id,p.principal_id LIMIT ?",
                (now_stamp, now_stamp, self.scopes_per_tick),
            ).fetchall()
        return tuple((row["workspace_id"], row["principal_id"]) for row in rows)

    def _claim(self, workspace_id: str, principal_id: str,
               now: datetime) -> tuple[str, int, int, int] | None:
        now_stamp = _stamp(now)
        lease_until = _stamp(now + timedelta(seconds=self.lease_seconds))
        token = uuid4().hex
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                pref = connection.execute(
                    "SELECT * FROM proactivity_preferences WHERE workspace_id=? AND principal_id=?",
                    (workspace_id, principal_id),
                ).fetchone()
                if pref is None or not pref["enabled"]:
                    self.db._commit(connection)
                    return None
                permissions = {row[0] for row in connection.execute(
                    "SELECT permission FROM workspace_permissions WHERE workspace_id=? AND principal_id=?",
                    (workspace_id, principal_id),
                ).fetchall()}
                if not {Permission.READ.value, Permission.WRITE.value} <= permissions:
                    self.db._commit(connection)
                    return None
                claim = connection.execute(
                    "SELECT * FROM proactivity_scheduler_claims WHERE workspace_id=? AND principal_id=?",
                    (workspace_id, principal_id),
                ).fetchone()
                if claim is not None and claim["generation"] == pref["generation"]:
                    live_lease = (claim["lease_expires_at"] is not None
                                  and _parse_stamp(claim["lease_expires_at"]) > now)
                    if live_lease:
                        self.db._commit(connection)
                        return None
                    if (claim["next_check_at"] is not None
                            and _parse_stamp(claim["next_check_at"]) > now):
                        self.db._commit(connection)
                        return None
                connection.execute(
                    "INSERT INTO proactivity_scheduler_claims(workspace_id,principal_id,generation,"
                    "claim_token,lease_expires_at,next_check_at,last_checked_at,last_suggestion_at) "
                    "VALUES(?,?,?,?,?,NULL,NULL,NULL) ON CONFLICT(workspace_id,principal_id) "
                    "DO UPDATE SET generation=excluded.generation,claim_token=excluded.claim_token,"
                    "lease_expires_at=excluded.lease_expires_at",
                    (workspace_id, principal_id, pref["generation"], token, lease_until),
                )
                self.db._commit(connection)
                return token, int(pref["generation"]), int(pref["check_interval_seconds"]), int(pref["cooldown_seconds"])
            except Exception:
                self.db._rollback(connection)
                raise

    def _scan(self, workspace_id: str, principal_id: str,
              now: datetime) -> tuple[tuple[_Candidate, ...], int]:
        with self.db._connection() as connection:
            rows = connection.execute(
                "SELECT t.id,t.workspace_id,t.principal_id,t.state AS task_state,t.updated_at AS task_updated_at,"
                "t.current_plan_revision,t.repository_commit,t.archived_at AS task_archived_at,"
                "m.id AS mission_id,m.state AS mission_state,m.updated_at AS mission_updated_at,"
                "m.archived_at AS mission_archived_at,m.created_at AS mission_created_at "
                "FROM orchestration_tasks t LEFT JOIN missions m ON m.task_id=t.id "
                "AND m.workspace_id=t.workspace_id AND m.principal_id=t.principal_id "
                "WHERE t.workspace_id=? AND t.principal_id=? AND t.archived_at IS NULL "
                "AND (m.id IS NULL OR m.archived_at IS NULL) "
                "ORDER BY t.updated_at,t.id LIMIT ?",
                (workspace_id, principal_id, MAX_SCANNED_RECORDS_PER_SCOPE),
            ).fetchall()
            candidates: list[_Candidate] = []
            threshold = now - timedelta(seconds=UNFINISHED_MISSION_SECONDS)
            for row in rows:
                mission_state = row["mission_state"]
                task_state = row["task_state"]
                reason = None
                if mission_state in _MISSION_STATES:
                    reason = f"mission_{mission_state}"
                elif task_state in _TASK_STATES:
                    reason = f"task_{task_state}"
                elif mission_state in _MISSION_UNFINISHED:
                    try:
                        if _parse_stamp(row["mission_updated_at"]) <= threshold:
                            reason = "mission_aged"
                    except ValueError:
                        continue
                if reason is None:
                    continue
                source = self._source_row(connection, workspace_id, principal_id,
                                          row["id"], row["mission_id"] if reason.startswith("mission_") else None)
                if source is None:
                    continue
                source_kind = "mission" if reason.startswith("mission_") else "task"
                candidates.append(_Candidate(
                    "mission_follow_up" if source_kind == "mission" else "task_follow_up",
                    reason, source_kind, row["id"],
                    row["mission_id"] if source_kind == "mission" else None,
                    self._source_digest(source),
                ))
            return tuple(candidates), len(rows)

    def _commit_tick(self, workspace_id: str, principal_id: str, token: str,
                     generation: int, interval: int, cooldown: int,
                     candidates: tuple[_Candidate, ...], scanned: int,
                     now: datetime) -> dict[str, int]:
        now_stamp = _stamp(now)
        next_check = _stamp(now + timedelta(seconds=interval))
        created = retracted = expired = 0
        with self.db._connection() as connection:
            self.db._begin(connection)
            try:
                pref = connection.execute(
                    "SELECT enabled,generation FROM proactivity_preferences "
                    "WHERE workspace_id=? AND principal_id=?", (workspace_id, principal_id),
                ).fetchone()
                claim = connection.execute(
                    "SELECT * FROM proactivity_scheduler_claims WHERE workspace_id=? AND principal_id=?",
                    (workspace_id, principal_id),
                ).fetchone()
                permissions = {row[0] for row in connection.execute(
                    "SELECT permission FROM workspace_permissions WHERE workspace_id=? AND principal_id=?",
                    (workspace_id, principal_id),
                ).fetchall()}
                if (pref is None or not pref["enabled"] or pref["generation"] != generation
                        or claim is None or claim["claim_token"] != token
                        or claim["generation"] != generation
                        or not claim["lease_expires_at"]
                        or _parse_stamp(claim["lease_expires_at"]) <= now
                        or not {Permission.READ.value, Permission.WRITE.value} <= permissions):
                    self.db._rollback(connection)
                    return {"scanned": scanned, "created": 0, "retracted": 0,
                            "expired": 0, "stale_worker": 1}

                old = connection.execute(
                    "SELECT * FROM proactive_suggestions WHERE workspace_id=? AND principal_id=? "
                    "AND state IN ('active','snoozed') ORDER BY created_at,id LIMIT ?",
                    (workspace_id, principal_id, MAX_ACTIVE_SUGGESTIONS),
                ).fetchall()
                for row in old:
                    item = _row_suggestion(row)
                    if _parse_stamp(item.expires_at) <= now:
                        self._transition(connection, row, "expired", "suggestion_expired", now_stamp)
                        expired += 1
                    else:
                        source = self._source_row(connection, workspace_id, principal_id,
                                                  item.source_task_id, item.source_mission_id)
                        if source is None or self._source_digest(source) != row["source_revision_sha256"]:
                            self._transition(connection, row, "retracted", "suggestion_retracted", now_stamp)
                            retracted += 1

                active_count = int(connection.execute(
                    "SELECT COUNT(*) FROM proactive_suggestions WHERE workspace_id=? AND principal_id=? "
                    "AND state IN ('active','snoozed') AND expires_at>?",
                    (workspace_id, principal_id, now_stamp),
                ).fetchone()[0])
                last = _parse_stamp(claim["last_suggestion_at"]) if claim["last_suggestion_at"] else None
                for candidate in candidates:
                    if created >= MAX_NEW_SUGGESTIONS_PER_SCOPE_TICK or active_count >= MAX_ACTIVE_SUGGESTIONS:
                        break
                    if last is not None and (now - last).total_seconds() < cooldown:
                        continue
                    source = self._source_row(connection, workspace_id, principal_id,
                                              candidate.task_id, candidate.mission_id)
                    if source is None or self._source_digest(source) != candidate.source_digest:
                        continue
                    dedup = _digest({
                        "workspace_id": workspace_id, "principal_id": principal_id,
                        "type": candidate.suggestion_type, "reason": candidate.reason_code,
                        "task_id": candidate.task_id, "mission_id": candidate.mission_id,
                        "source_revision_sha256": candidate.source_digest,
                    })
                    if connection.execute(
                        "SELECT 1 FROM proactive_suggestions WHERE workspace_id=? AND principal_id=? "
                        "AND deduplication_key=?", (workspace_id, principal_id, dedup),
                    ).fetchone() is not None:
                        continue
                    suggestion_id = uuid4().hex
                    expires = _stamp(now + timedelta(days=DEFAULT_SUGGESTION_LIFETIME_DAYS))
                    connection.execute(
                        "INSERT INTO proactive_suggestions(id,workspace_id,principal_id,suggestion_type,"
                        "source_kind,source_task_id,source_mission_id,source_revision_sha256,"
                        "deduplication_key,state,created_at,expires_at,reason_code) "
                        "VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                        (suggestion_id, workspace_id, principal_id, candidate.suggestion_type,
                         candidate.source_kind, candidate.task_id, candidate.mission_id,
                         candidate.source_digest, dedup, now_stamp, expires, candidate.reason_code),
                    )
                    item = _row_suggestion(connection.execute(
                        "SELECT * FROM proactive_suggestions WHERE id=?", (suggestion_id,),
                    ).fetchone())
                    self._event(connection, item, "suggestion_created", now_stamp,
                                {"reason_code": candidate.reason_code})
                    self._audit(connection, principal_id, item, "suggestion_created")
                    created += 1
                    active_count += 1
                    last = now
                connection.execute(
                    "UPDATE proactivity_scheduler_claims SET claim_token=NULL,lease_expires_at=NULL,"
                    "last_checked_at=?,next_check_at=?,last_suggestion_at=? WHERE workspace_id=? "
                    "AND principal_id=? AND claim_token=? AND generation=?",
                    (now_stamp, next_check, _stamp(last) if last else claim["last_suggestion_at"],
                     workspace_id, principal_id, token, generation),
                )
                self.db._commit(connection)
                return {"scanned": scanned, "created": created, "retracted": retracted,
                        "expired": expired, "stale_worker": 0}
            except Exception:
                self.db._rollback(connection)
                raise

    def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        """Run one synchronous, bounded scheduler pass; no thread or model is started."""
        effective_now = _utc(now) if now is not None else self._now()
        totals = {"scopes_processed": 0, "scanned": 0, "created": 0,
                  "retracted": 0, "expired": 0, "stale_worker": 0}
        for workspace_id, principal_id in self._eligible_scopes(effective_now):
            claim = self._claim(workspace_id, principal_id, effective_now)
            if claim is None:
                continue
            token, generation, interval, cooldown = claim
            candidates, scanned = self._scan(workspace_id, principal_id, effective_now)
            result = self._commit_tick(workspace_id, principal_id, token, generation,
                                       interval, cooldown, candidates, scanned, effective_now)
            totals["scopes_processed"] += 1
            for key, value in result.items():
                totals[key] += value
        return totals

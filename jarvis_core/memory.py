"""Workspace-scoped approval and reversible lifecycle for durable memories.

DecisionContextIssuer is a trusted-host capability. Keep its signing key in the
host, never expose it through an untrusted request. A valid signature proves
that the host issued a decision context; it does not prove a human clicked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import sqlite3
from typing import Iterable
from uuid import uuid4

from .retrieval import (
    MAX_EXCERPT_CHARS,
    MAX_RESULTS,
    _fts_expression,
    _terms,
)
from .storage import AccessDenied, Database, Permission, _now, _nonempty

MAX_CONTENT_CHARS = 50_000
MAX_EVIDENCE = 25
_CONTEXT_MAX_AGE = timedelta(minutes=5)


class LifecycleConflict(RuntimeError):
    """Requested lifecycle transition is stale or not allowed from this state."""


class InvalidDecision(PermissionError):
    """Decision context is missing, invalid, expired, or already consumed."""


@dataclass(frozen=True)
class Evidence:
    document_id: str
    root_id: str
    relative_path: str
    source_sha256: str
    observed_at: str


@dataclass(frozen=True)
class Candidate:
    id: str
    workspace_id: str
    proposed_by: str
    provenance_kind: str
    content: str | None
    content_sha256: str
    evidence_sha256: str
    revision: int
    state: str
    created_at: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Memory:
    id: str
    workspace_id: str
    content: str
    content_sha256: str
    provenance_kind: str
    created_by: str
    approved_by: str
    created_at: str
    updated_at: str
    revision: int
    state_version: int
    deleted_at: str | None
    evidence: tuple[Evidence, ...]

    @property
    def evidence_sha256(self) -> str:
        return _evidence_digest(self.provenance_kind, self.evidence)


@dataclass(frozen=True)
class MemoryRevision:
    memory_id: str
    revision: int
    content: str
    content_sha256: str
    edited_by: str
    created_at: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MemorySearchResult:
    memory_id: str
    workspace_id: str
    excerpt: str
    score: float
    revision: int
    provenance_kind: str
    source_status: str
    evidence: tuple[Evidence, ...]


class MemorySearchUnavailable(RuntimeError):
    """The separate approved-memory FTS5 index is missing or unusable."""


@dataclass(frozen=True)
class DecisionContext:
    action: str
    actor_id: str
    workspace_id: str
    resource_id: str
    resource_version: int
    content_sha256: str
    evidence_sha256: str
    decision_id: str
    issued_at: str
    signature: str


class DecisionContextIssuer:
    """Trusted-host signer for the exact snapshot rendered to a user."""

    def __init__(self, signing_key: bytes):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("decision signing key must contain at least 32 bytes")
        self._key = signing_key

    def issue(self, *, action: str, actor_id: str, workspace_id: str,
              resource_id: str, resource_version: int,
              content_sha256: str, evidence_sha256: str) -> DecisionContext:
        if action not in {"approve_candidate", "reject_candidate", "purge_memory"}:
            raise ValueError("unsupported decision action")
        actor_id=_nonempty(actor_id,"actor ID"); workspace_id=_nonempty(workspace_id,"workspace ID")
        resource_id=_nonempty(resource_id,"resource ID")
        if not isinstance(resource_version,int) or isinstance(resource_version,bool) or resource_version < 1:
            raise ValueError("resource version must be a positive integer")
        if not all(isinstance(v,str) and len(v)==64 and all(c in "0123456789abcdef" for c in v) for v in (content_sha256,evidence_sha256)):
            raise ValueError("decision snapshot digests must be lowercase SHA-256 hex")
        fields = (action, actor_id, workspace_id, resource_id, resource_version,
                  content_sha256, evidence_sha256, uuid4().hex, _now())
        signature = hmac.new(self._key, _canonical(fields), hashlib.sha256).hexdigest()
        return DecisionContext(*fields, signature)


def _canonical(fields: tuple) -> bytes:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _context_fields(context: DecisionContext) -> tuple:
    return (context.action, context.actor_id, context.workspace_id,
            context.resource_id, context.resource_version,
            context.content_sha256, context.evidence_sha256,
            context.decision_id, context.issued_at)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()


def _validate_content(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("memory content must be text")
    if not content.strip() or len(content) > MAX_CONTENT_CHARS or "\x00" in content:
        raise ValueError(f"memory content must contain 1 to {MAX_CONTENT_CHARS} safe characters")
    content.encode("utf-8", errors="strict")
    return content


def _evidence_digest(kind: str, rows: Iterable[Evidence]) -> str:
    entries = sorted((e.document_id, e.root_id, e.relative_path, e.source_sha256, e.observed_at) for e in rows)
    return _hash(json.dumps([kind, entries], ensure_ascii=False, separators=(",", ":")))


class MemoryService:
    """Durable memory lifecycle. Every operation takes actor and workspace IDs."""

    def __init__(self, database: Database, decision_verification_key: bytes):
        if not isinstance(decision_verification_key, bytes) or len(decision_verification_key) < 32:
            raise ValueError("decision verification key must contain at least 32 bytes")
        self.db = database
        self._verification_key = decision_verification_key

    def _verify_context(self, context: DecisionContext, *, action: str,
                        actor_id: str, workspace_id: str, resource_id: str,
                        version: int, content_sha256: str, evidence_sha256: str) -> str:
        if not isinstance(context, DecisionContext):
            raise InvalidDecision("a trusted-host decision context is required")
        if (not all(isinstance(v,str) for v in (context.action,context.actor_id,context.workspace_id,
                context.resource_id,context.content_sha256,context.evidence_sha256,
                context.decision_id,context.issued_at,context.signature))
                or not isinstance(context.resource_version,int) or isinstance(context.resource_version,bool)
                or context.resource_version < 1 or len(context.signature)!=64):
            raise InvalidDecision("decision context is malformed")
        fields = _context_fields(context)
        expected = hmac.new(self._verification_key, _canonical(fields), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, context.signature):
            raise InvalidDecision("decision context signature is invalid")
        if (context.action, context.actor_id, context.workspace_id, context.resource_id,
            context.resource_version, context.content_sha256, context.evidence_sha256) != (
            action, actor_id, workspace_id, resource_id, version, content_sha256, evidence_sha256):
            raise InvalidDecision("decision context does not match the reviewed snapshot")
        try:
            issued = datetime.fromisoformat(context.issued_at)
        except (TypeError, ValueError):
            raise InvalidDecision("decision context timestamp is invalid") from None
        now = datetime.now(timezone.utc)
        if issued.tzinfo is None or issued > now + timedelta(seconds=30) or now - issued > _CONTEXT_MAX_AGE:
            raise InvalidDecision("decision context is expired")
        return _hash(json.dumps(fields, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _read_evidence(connection: sqlite3.Connection, table: str,
                       resource_column: str, resource_id: str) -> tuple[Evidence, ...]:
        rows = connection.execute(
            f"SELECT document_id,root_id,relative_path,source_sha256,observed_at FROM {table} WHERE {resource_column}=? ORDER BY document_id",
            (resource_id,),
        ).fetchall()
        return tuple(Evidence(*tuple(row)) for row in rows)

    def _candidate(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Candidate:
        return Candidate(row["id"], row["workspace_id"], row["proposed_by"],
                         row["provenance_kind"], row["content"], row["content_sha256"],
                         row["evidence_sha256"], row["revision"], row["state"], row["created_at"],
                         self._read_evidence(connection, "memory_candidate_evidence", "candidate_id", row["id"]))

    def _memory(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Memory:
        return Memory(row["id"], row["workspace_id"], row["content"], row["content_sha256"],
                      row["provenance_kind"], row["created_by"], row["approved_by"],
                      row["created_at"], row["updated_at"], row["revision"],
                      row["state_version"], row["deleted_at"],
                      self._read_evidence(connection, "memory_evidence", "memory_id", row["id"]))

    @staticmethod
    def _load_candidate(connection: sqlite3.Connection, workspace_id: str, candidate_id: str):
        row = connection.execute("SELECT * FROM memory_candidates WHERE id=? AND workspace_id=?",
                                  (candidate_id, workspace_id)).fetchone()
        if row is None:
            raise LifecycleConflict("candidate is unavailable")
        return row

    @staticmethod
    def _load_memory(connection: sqlite3.Connection, workspace_id: str, memory_id: str):
        row = connection.execute("SELECT * FROM memories WHERE id=? AND workspace_id=?",
                                  (memory_id, workspace_id)).fetchone()
        if row is None:
            raise LifecycleConflict("memory is unavailable")
        return row

    def _capture_evidence(self, connection: sqlite3.Connection, workspace_id: str,
                          document_ids: Iterable[str], user_authored: bool) -> tuple[str, tuple[Evidence, ...]]:
        ids = tuple(dict.fromkeys(_nonempty(i, "document ID", 200) for i in document_ids))
        if len(ids) > MAX_EVIDENCE:
            raise ValueError(f"at most {MAX_EVIDENCE} evidence documents are allowed")
        if user_authored and ids:
            raise ValueError("choose evidence provenance or user-authored provenance")
        if not ids and not user_authored:
            raise ValueError("memory requires evidence or explicit user-authored designation")
        kind = "user_authored" if user_authored else "evidence"
        evidence: list[Evidence] = []
        for doc_id in ids:
            row = connection.execute("""SELECT d.id,d.workspace_id,d.root_id,d.relative_path,
                d.content_sha256,d.last_seen_at,r.revoked_at FROM ingested_documents d
                JOIN approved_roots r ON r.id=d.root_id AND r.workspace_id=d.workspace_id
                WHERE d.id=? AND d.workspace_id=? AND d.deleted_at IS NULL""", (doc_id,workspace_id)).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise AccessDenied("evidence is unavailable in this workspace")
            evidence.append(Evidence(row["id"],row["root_id"],row["relative_path"],row["content_sha256"],row["last_seen_at"]))
        return kind, tuple(evidence)

    @staticmethod
    def _insert_evidence(connection, table: str, resource_col: str,
                         resource_id: str, workspace_id: str, evidence: Iterable[Evidence],
                         revision: int | None = None):
        for item in evidence:
            if revision is None:
                connection.execute(f"INSERT INTO {table} ({resource_col},workspace_id,document_id,root_id,relative_path,source_sha256,observed_at) VALUES (?,?,?,?,?,?,?)",
                                   (resource_id,workspace_id,item.document_id,item.root_id,item.relative_path,item.source_sha256,item.observed_at))
            else:
                connection.execute("INSERT INTO memory_revision_evidence (memory_id,revision,document_id,root_id,relative_path,source_sha256,observed_at) VALUES (?,?,?,?,?,?,?)",
                                   (resource_id,revision,item.document_id,item.root_id,item.relative_path,item.source_sha256,item.observed_at))

    def propose(self, actor_id: str, workspace_id: str, content: str, *,
                evidence_document_ids: Iterable[str] = (), user_authored: bool = False,
                request_key: str | None = None) -> Candidate:
        actor_id=_nonempty(actor_id,"actor ID"); workspace_id=_nonempty(workspace_id,"workspace ID")
        if not isinstance(user_authored,bool): raise TypeError("user_authored must be a boolean designation")
        content=_validate_content(content); content_hash=_hash(content)
        if request_key is not None: request_key=_nonempty(request_key,"request key",200)
        self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
                kind,evidence=self._capture_evidence(c,workspace_id,evidence_document_ids,user_authored)
                digest=_evidence_digest(kind,evidence)
                if request_key is not None:
                    existing=c.execute("SELECT * FROM memory_candidates WHERE workspace_id=? AND proposed_by=? AND request_key=?",(workspace_id,actor_id,request_key)).fetchone()
                    if existing:
                        if existing["state"] != "pending":
                            raise LifecycleConflict("proposal request was already resolved")
                        if existing["content_sha256"]!=content_hash or existing["evidence_sha256"]!=digest or existing["provenance_kind"]!=kind:
                            raise LifecycleConflict("request key was already used for a different proposal")
                        result=self._candidate(c,existing); self.db._commit(c); return result
                cid=uuid4().hex; now=_now()
                c.execute("INSERT INTO memory_candidates(id,workspace_id,proposed_by,request_key,provenance_kind,content,content_sha256,evidence_sha256,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (cid,workspace_id,actor_id,request_key,kind,content,content_hash,digest,"pending",now,now))
                self._insert_evidence(c,"memory_candidate_evidence","candidate_id",cid,workspace_id,evidence)
                self.db._insert_audit(c,workspace_id,actor_id,"memory.proposed","memory_candidate",cid,{"provenance":kind,"evidence_count":str(len(evidence))})
                row=self._load_candidate(c,workspace_id,cid); result=self._candidate(c,row); self.db._commit(c); return result
            except Exception: self.db._rollback(c); raise

    def inspect_candidate(self, actor_id: str, workspace_id: str, candidate_id: str) -> Candidate:
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.READ)
            row=self._load_candidate(c,workspace_id,candidate_id)
            if row["state"]!="pending": raise LifecycleConflict("candidate is no longer pending")
            return self._candidate(c,row)

    def list_candidates(self, actor_id: str, workspace_id: str, *, state: str="pending", limit: int=100) -> list[Candidate]:
        if state not in {"pending","approved","rejected"} or not 1<=limit<=500: raise ValueError("invalid candidate list filter")
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.READ)
            rows=c.execute("SELECT * FROM memory_candidates WHERE workspace_id=? AND state=? ORDER BY created_at,id LIMIT ?",(workspace_id,state,limit)).fetchall()
            return [self._candidate(c,r) for r in rows]

    def edit_candidate(self, actor_id: str, workspace_id: str, candidate_id: str, *, expected_revision: int,
                       content: str, evidence_document_ids: Iterable[str]=(), user_authored: bool=False) -> Candidate:
        actor_id=_nonempty(actor_id,"actor ID"); content=_validate_content(content); ch=_hash(content)
        self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
                old=self._load_candidate(c,workspace_id,candidate_id)
                if old["state"]!="pending" or old["revision"]!=expected_revision: raise LifecycleConflict("candidate changed or is no longer pending")
                kind,ev=self._capture_evidence(c,workspace_id,evidence_document_ids,user_authored); eh=_evidence_digest(kind,ev)
                c.execute("DELETE FROM memory_candidate_evidence WHERE candidate_id=?",(candidate_id,))
                self._insert_evidence(c,"memory_candidate_evidence","candidate_id",candidate_id,workspace_id,ev)
                c.execute("UPDATE memory_candidates SET content=?,content_sha256=?,evidence_sha256=?,provenance_kind=?,revision=revision+1,updated_at=? WHERE id=? AND workspace_id=? AND state='pending' AND revision=?",
                          (content,ch,eh,kind,_now(),candidate_id,workspace_id,expected_revision))
                self.db._insert_audit(c,workspace_id,actor_id,"memory.candidate_edited","memory_candidate",candidate_id,{"revision":str(expected_revision+1),"provenance":kind,"evidence_count":str(len(ev))})
                result=self._candidate(c,self._load_candidate(c,workspace_id,candidate_id)); self.db._commit(c); return result
            except Exception: self.db._rollback(c); raise

    def _consume_decision(self,c,context):
        # Retain a replay/audit receipt without retaining a content-derived hash.
        receipt_hash=_hash(json.dumps((context.action,context.actor_id,context.workspace_id,
            context.resource_id,context.resource_version,context.decision_id),separators=(",",":")))
        try:
            c.execute("INSERT INTO memory_decision_receipts(decision_id,action,workspace_id,actor_id,resource_id,context_sha256,consumed_at) VALUES(?,?,?,?,?,?,?)",
                      (context.decision_id,context.action,context.workspace_id,context.actor_id,context.resource_id,receipt_hash,_now()))
        except sqlite3.IntegrityError: raise InvalidDecision("decision context has already been used") from None

    def approve(self, actor_id: str, workspace_id: str, candidate_id: str, decision: DecisionContext) -> Memory:
        actor_id=_nonempty(actor_id,"actor ID"); self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
                row=self._load_candidate(c,workspace_id,candidate_id)
                if row["state"]!="pending": raise LifecycleConflict("candidate is no longer pending")
                ev=self._read_evidence(c,"memory_candidate_evidence","candidate_id",candidate_id)
                self._verify_context(decision,action="approve_candidate",actor_id=actor_id,workspace_id=workspace_id,resource_id=candidate_id,version=row["revision"],content_sha256=row["content_sha256"],evidence_sha256=row["evidence_sha256"])
                if _hash(row["content"])!=row["content_sha256"] or _evidence_digest(row["provenance_kind"],ev)!=row["evidence_sha256"]:
                    raise LifecycleConflict("candidate snapshot has changed")
                try:
                    current_kind,current_ev=self._capture_evidence(c,workspace_id,(e.document_id for e in ev),row["provenance_kind"]=="user_authored")
                except AccessDenied:
                    raise LifecycleConflict("candidate evidence has changed or is unavailable") from None
                original_identity={(e.document_id,e.root_id,e.relative_path,e.source_sha256) for e in ev}
                current_identity={(e.document_id,e.root_id,e.relative_path,e.source_sha256) for e in current_ev}
                if current_kind!=row["provenance_kind"] or original_identity!=current_identity:
                    raise LifecycleConflict("candidate evidence has changed or is unavailable")
                self._consume_decision(c,decision)
                mid=uuid4().hex; now=_now()
                c.execute("INSERT INTO memories(id,workspace_id,source_candidate_id,content,content_sha256,provenance_kind,created_by,approved_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (mid,workspace_id,candidate_id,row["content"],row["content_sha256"],row["provenance_kind"],row["proposed_by"],actor_id,now,now))
                self._insert_evidence(c,"memory_evidence","memory_id",mid,workspace_id,ev)
                c.execute("INSERT INTO memory_revisions(memory_id,workspace_id,revision,content,content_sha256,provenance_kind,edited_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (mid,workspace_id,1,row["content"],row["content_sha256"],row["provenance_kind"],actor_id,now))
                self._insert_evidence(c,"memory_revision_evidence","memory_id",mid,workspace_id,ev,revision=1)
                c.execute("UPDATE memory_candidates SET state='approved',content=NULL,approved_memory_id=?,decided_at=?,updated_at=? WHERE id=? AND state='pending'",
                          (mid,now,now,candidate_id))
                c.execute("DELETE FROM memory_candidate_evidence WHERE candidate_id=?",(candidate_id,))
                self.db._insert_audit(c,workspace_id,actor_id,"memory.approved","memory",mid,{"provenance":row["provenance_kind"],"evidence_count":str(len(ev))})
                result=self._memory(c,self._load_memory(c,workspace_id,mid)); self.db._commit(c); return result
            except Exception: self.db._rollback(c); raise

    def reject(self, actor_id: str, workspace_id: str, candidate_id: str, decision: DecisionContext) -> None:
        actor_id=_nonempty(actor_id,"actor ID"); self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
                row=self._load_candidate(c,workspace_id,candidate_id)
                if row["state"]!="pending": raise LifecycleConflict("candidate is no longer pending")
                ev=self._read_evidence(c,"memory_candidate_evidence","candidate_id",candidate_id)
                if _hash(row["content"])!=row["content_sha256"] or _evidence_digest(row["provenance_kind"],ev)!=row["evidence_sha256"]: raise LifecycleConflict("candidate snapshot has changed")
                self._verify_context(decision,action="reject_candidate",actor_id=actor_id,workspace_id=workspace_id,resource_id=candidate_id,version=row["revision"],content_sha256=row["content_sha256"],evidence_sha256=row["evidence_sha256"])
                self._consume_decision(c,decision)
                c.execute("UPDATE memory_candidates SET state='rejected',content=NULL,decided_at=?,updated_at=? WHERE id=? AND state='pending'",(_now(),_now(),candidate_id))
                c.execute("DELETE FROM memory_candidate_evidence WHERE candidate_id=?",(candidate_id,))
                self.db._insert_audit(c,workspace_id,actor_id,"memory.rejected","memory_candidate",candidate_id,{"provenance":row["provenance_kind"],"evidence_count":str(len(ev))})
                self.db._commit(c)
            except Exception: self.db._rollback(c); raise

    def get(self, actor_id: str, workspace_id: str, memory_id: str) -> Memory:
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.READ)
            row=self._load_memory(c,workspace_id,memory_id)
            if row["deleted_at"] is not None: raise LifecycleConflict("memory is deleted")
            return self._memory(c,row)

    def list_memories(self, actor_id: str, workspace_id: str, *, limit: int=100) -> list[Memory]:
        if not 1<=limit<=500: raise ValueError("limit must be between 1 and 500")
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.READ)
            rows=c.execute("SELECT * FROM memories WHERE workspace_id=? AND deleted_at IS NULL ORDER BY updated_at,id LIMIT ?",(workspace_id,limit)).fetchall()
            return [self._memory(c,r) for r in rows]

    def search(self, actor_id: str, workspace_id: str, query: str, *, limit: int = 10) -> list[MemorySearchResult]:
        """Search approved, active memories in one authorized workspace.

        Original evidence rows are returned as immutable provenance snapshots.
        ``source_status`` is ``active_sources`` only when every source still
        matches its approved path/content snapshot and has an active root;
        otherwise the approved memory is explicitly marked historical.
        """
        actor_id = _nonempty(actor_id, "actor ID")
        workspace_id = _nonempty(workspace_id, "workspace ID")
        try:
            self.db.initialize()
        except RuntimeError as exc:
            if "requires SQLite built with FTS5" in str(exc):
                raise MemorySearchUnavailable(
                    "approved-memory search requires an SQLite build with FTS5"
                ) from exc
            raise
        with self.db._connection() as connection:
            # Preserve SearchIndex's authorization-before-input-validation rule.
            Database._require_access(connection, actor_id, workspace_id, Permission.READ)
            if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
                raise ValueError(f"limit must be an integer from 1 to {MAX_RESULTS}")
            terms = _terms(query)
            if not terms:
                return []
            expression = _fts_expression(terms)
            try:
                rows = connection.execute(
                    """
                    SELECT m.id AS memory_id,
                           m.workspace_id AS workspace_id,
                           m.revision AS revision,
                           m.provenance_kind AS provenance_kind,
                           substr(
                               snippet(memory_search, 2, '', '', '…', 16),
                               1, ?
                           ) AS excerpt,
                           bm25(memory_search, 0.0, 0.0, 1.0) AS score
                    FROM memory_search
                    JOIN memories AS m
                      ON m.id = memory_search.memory_id
                     AND m.workspace_id = memory_search.workspace_id
                    WHERE memory_search MATCH ?
                      AND memory_search.workspace_id = ?
                      AND m.workspace_id = ?
                      AND m.deleted_at IS NULL
                    ORDER BY score ASC, m.id ASC
                    LIMIT ?
                    """,
                    (MAX_EXCERPT_CHARS, expression, workspace_id, workspace_id, limit),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "memory_search" in message or "no such module: fts5" in message:
                    raise MemorySearchUnavailable(
                        "approved-memory FTS5 index is unavailable; initialize or rebuild it"
                    ) from exc
                raise

            result: list[MemorySearchResult] = []
            for row in rows:
                provenance = connection.execute(
                    """
                    SELECT e.document_id, e.root_id, e.relative_path,
                           e.source_sha256, e.observed_at,
                           CASE WHEN d.id IS NOT NULL
                                     AND d.deleted_at IS NULL
                                     AND r.revoked_at IS NULL
                                     AND d.relative_path = e.relative_path
                                     AND d.content_sha256 = e.source_sha256
                                THEN 1 ELSE 0 END AS source_active
                    FROM memory_evidence AS e
                    LEFT JOIN ingested_documents AS d
                      ON d.id = e.document_id
                     AND d.workspace_id = e.workspace_id
                     AND d.root_id = e.root_id
                    LEFT JOIN approved_roots AS r
                      ON r.id = e.root_id AND r.workspace_id = e.workspace_id
                    WHERE e.memory_id = ? AND e.workspace_id = ?
                    ORDER BY e.document_id
                    """,
                    (row["memory_id"], workspace_id),
                ).fetchall()
                evidence = tuple(
                    Evidence(
                        item["document_id"], item["root_id"], item["relative_path"],
                        item["source_sha256"], item["observed_at"],
                    )
                    for item in provenance
                )
                if row["provenance_kind"] == "user_authored":
                    source_status = "user_authored"
                elif evidence and all(item["source_active"] for item in provenance):
                    source_status = "active_sources"
                else:
                    source_status = "historical_sources"
                result.append(
                    MemorySearchResult(
                        memory_id=row["memory_id"],
                        workspace_id=row["workspace_id"],
                        excerpt=row["excerpt"],
                        score=float(row["score"]),
                        revision=int(row["revision"]),
                        provenance_kind=row["provenance_kind"],
                        source_status=source_status,
                        evidence=evidence,
                    )
                )
            return result

    def list_deleted(self, actor_id: str, workspace_id: str, *, limit: int=100) -> list[Memory]:
        if not 1<=limit<=500: raise ValueError("limit must be between 1 and 500")
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
            rows=c.execute("SELECT * FROM memories WHERE workspace_id=? AND deleted_at IS NOT NULL ORDER BY deleted_at,id LIMIT ?",(workspace_id,limit)).fetchall()
            return [self._memory(c,r) for r in rows]

    def edit(self, actor_id: str, workspace_id: str, memory_id: str, *, expected_revision: int, content: str) -> Memory:
        actor_id=_nonempty(actor_id,"actor ID"); content=_validate_content(content); ch=_hash(content); self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
                old=self._load_memory(c,workspace_id,memory_id)
                if old["deleted_at"] is not None or old["revision"]!=expected_revision: raise LifecycleConflict("memory changed or is unavailable")
                evidence=self._read_evidence(c,"memory_evidence","memory_id",memory_id); rev=expected_revision+1; now=_now()
                c.execute("INSERT INTO memory_revisions(memory_id,workspace_id,revision,content,content_sha256,provenance_kind,edited_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (memory_id,workspace_id,rev,content,ch,old["provenance_kind"],actor_id,now))
                self._insert_evidence(c,"memory_revision_evidence","memory_id",memory_id,workspace_id,evidence,revision=rev)
                c.execute("UPDATE memories SET content=?,content_sha256=?,revision=?,state_version=state_version+1,updated_at=? WHERE id=? AND workspace_id=? AND revision=? AND deleted_at IS NULL",
                          (content,ch,rev,now,memory_id,workspace_id,expected_revision))
                self.db._insert_audit(c,workspace_id,actor_id,"memory.edited","memory",memory_id,{"revision":str(rev),"provenance":old["provenance_kind"],"evidence_count":str(len(evidence))})
                result=self._memory(c,self._load_memory(c,workspace_id,memory_id)); self.db._commit(c); return result
            except Exception: self.db._rollback(c); raise

    @staticmethod
    def _revision_history(connection: sqlite3.Connection, workspace_id: str,
                          memory_id: str) -> list[MemoryRevision]:
        rows=connection.execute(
            "SELECT * FROM memory_revisions WHERE memory_id=? AND workspace_id=? ORDER BY revision",
            (memory_id,workspace_id),
        ).fetchall()
        result=[]
        for row in rows:
            evidence_rows=connection.execute(
                "SELECT document_id,root_id,relative_path,source_sha256,observed_at "
                "FROM memory_revision_evidence WHERE memory_id=? AND revision=? ORDER BY document_id",
                (row["memory_id"],row["revision"]),
            ).fetchall()
            evidence=tuple(Evidence(*tuple(item)) for item in evidence_rows)
            result.append(MemoryRevision(row["memory_id"],row["revision"],row["content"],
                row["content_sha256"],row["edited_by"],row["created_at"],evidence))
        return result

    def list_revisions(self, actor_id: str, workspace_id: str, memory_id: str) -> list[MemoryRevision]:
        """List history for active memories only; deleted content uses recovery access."""
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.READ)
            memory=self._load_memory(c,workspace_id,memory_id)
            if memory["deleted_at"] is not None:
                raise LifecycleConflict("memory is deleted; use authorized recovery history")
            return self._revision_history(c,workspace_id,memory_id)

    def list_deleted_revisions(self, actor_id: str, workspace_id: str,
                               memory_id: str) -> list[MemoryRevision]:
        """Read deleted-memory history only with write access for recovery."""
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.WRITE)
            memory=self._load_memory(c,workspace_id,memory_id)
            if memory["deleted_at"] is None:
                raise LifecycleConflict("memory is not deleted")
            return self._revision_history(c,workspace_id,memory_id)

    def soft_delete(self, actor_id: str, workspace_id: str, memory_id: str, *, expected_state_version: int) -> Memory:
        return self._set_deleted(actor_id,workspace_id,memory_id,expected_state_version,True)

    def restore(self, actor_id: str, workspace_id: str, memory_id: str, *, expected_state_version: int) -> Memory:
        return self._set_deleted(actor_id,workspace_id,memory_id,expected_state_version,False)

    def _set_deleted(self,actor_id,workspace_id,memory_id,expected_state_version,deleted):
        self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.WRITE); row=self._load_memory(c,workspace_id,memory_id)
                if row["state_version"]!=expected_state_version or (row["deleted_at"] is not None)==deleted: raise LifecycleConflict("memory state changed")
                when=_now() if deleted else None
                c.execute("UPDATE memories SET deleted_at=?,state_version=state_version+1,updated_at=? WHERE id=? AND workspace_id=? AND state_version=?",(when,_now(),memory_id,workspace_id,expected_state_version))
                event="memory.deleted" if deleted else "memory.restored"
                self.db._insert_audit(c,workspace_id,actor_id,event,"memory",memory_id,{"state_version":str(expected_state_version+1)})
                result=self._memory(c,self._load_memory(c,workspace_id,memory_id)); self.db._commit(c); return result
            except Exception: self.db._rollback(c); raise

    def purge_snapshot(self, actor_id: str, workspace_id: str, memory_id: str) -> Memory:
        """Return exact deleted-memory fields the host must display before purge."""
        self.db.initialize()
        with self.db._connection() as c:
            self.db._require_access(c,actor_id,workspace_id,Permission.MANAGE_WORKSPACE)
            row=self._load_memory(c,workspace_id,memory_id)
            if row["deleted_at"] is None: raise LifecycleConflict("only deleted memories can be purged")
            return self._memory(c,row)

    def purge(self, actor_id: str, workspace_id: str, memory_id: str, *, decision: DecisionContext) -> None:
        actor_id=_nonempty(actor_id,"actor ID"); self.db.initialize()
        with self.db._connection() as c:
            self.db._begin(c)
            try:
                self.db._require_access(c,actor_id,workspace_id,Permission.MANAGE_WORKSPACE); row=self._load_memory(c,workspace_id,memory_id)
                if row["deleted_at"] is None: raise LifecycleConflict("only deleted memories can be purged")
                evidence=self._read_evidence(c,"memory_evidence","memory_id",memory_id); eh=_evidence_digest(row["provenance_kind"],evidence)
                self._verify_context(decision,action="purge_memory",actor_id=actor_id,workspace_id=workspace_id,resource_id=memory_id,version=row["state_version"],content_sha256=row["content_sha256"],evidence_sha256=eh)
                self._consume_decision(c,decision)
                c.execute("UPDATE memory_candidates SET approved_memory_id=NULL,content_sha256='',evidence_sha256='' WHERE approved_memory_id=?",(memory_id,))
                c.execute("DELETE FROM memories WHERE id=? AND workspace_id=?",(memory_id,workspace_id))
                self.db._insert_audit(c,workspace_id,actor_id,"memory.purged","memory",memory_id,{"provenance":row["provenance_kind"],"evidence_count":str(len(evidence))})
                self.db._commit(c)
            except Exception: self.db._rollback(c); raise

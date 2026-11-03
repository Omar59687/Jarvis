"""Workspace-authorized offline lexical search over ingested documents."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import unicodedata

from .storage import AccessDenied, Database, Permission


MAX_QUERY_CHARS = 512
MAX_QUERY_TERMS = 32
MAX_RESULTS = 20
MAX_EXCERPT_CHARS = 400
_MAX_SNIPPET_TOKENS = 16


class FTS5Unavailable(RuntimeError):
    """The linked SQLite build does not provide the required FTS5 module."""


class SearchIndexUnavailable(RuntimeError):
    """The schema or local FTS index is missing or unusable."""


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    root_id: str
    relative_path: str
    excerpt: str
    score: float


def fts5_available() -> bool:
    """Probe the SQLite library used by Python without changing Jarvis data."""
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE VIRTUAL TABLE temp.jarvis_fts5_probe USING fts5(content)"
        )
        connection.execute("DROP TABLE temp.jarvis_fts5_probe")
        return True
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


def _terms(query: str) -> tuple[str, ...]:
    if not isinstance(query, str):
        raise ValueError("query must be text")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
    if "\x00" in query:
        raise ValueError("query contains a null character")
    try:
        query.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("query contains invalid Unicode") from exc

    normalized = unicodedata.normalize("NFKC", query)
    collected: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or (category[0] == "M" and current):
            current.append(character)
        elif current:
            collected.append("".join(current))
            current = []
    if current:
        collected.append("".join(current))

    unique: list[str] = []
    seen: set[str] = set()
    for term in collected:
        key = term.casefold()
        if key not in seen:
            unique.append(term)
            seen.add(key)
        if len(unique) >= MAX_QUERY_TERMS:
            break
    return tuple(unique)


def _fts_expression(terms: tuple[str, ...]) -> str:
    # Every term is a quoted FTS5 phrase. User punctuation and operators never
    # become FTS syntax; SQL receives this value through a bound parameter.
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


class SearchIndex:
    """Search active ingested documents in one explicitly authorized workspace."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def require_fts5() -> None:
        if not fts5_available():
            raise FTS5Unavailable(
                "Jarvis lexical search requires an SQLite build with FTS5 support"
            )

    def search(
        self, actor_id: str, workspace_id: str, query: str, limit: int = 10
    ) -> list[SearchResult]:
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id must be a non-empty string")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        try:
            self.database.initialize()
        except RuntimeError as exc:
            if "requires SQLite built with FTS5" in str(exc):
                raise FTS5Unavailable(
                    "Jarvis lexical search requires an SQLite build with FTS5 support"
                ) from exc
            raise
        with self.database._connection() as connection:
            # Check access before query validation or FTS capability reporting,
            # including for empty or malformed inputs.
            Database._require_access(
                connection, actor_id, workspace_id, Permission.READ
            )
            if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
                raise ValueError(f"limit must be an integer from 1 to {MAX_RESULTS}")
            self.require_fts5()
            terms = _terms(query)
            if not terms:
                return []
            expression = _fts_expression(terms)
            try:
                rows = connection.execute(
                    """
                    SELECT d.id AS document_id,
                           d.root_id AS root_id,
                           d.relative_path AS relative_path,
                           substr(
                               snippet(document_search, 4, '', '', '…', ?),
                               1, ?
                           ) AS excerpt,
                           bm25(document_search, 0.0, 0.0, 0.0, 0.0, 1.0) AS score
                    FROM document_search
                    JOIN ingested_documents AS d
                      ON d.id = document_search.document_id
                     AND d.workspace_id = document_search.workspace_id
                     AND d.root_id = document_search.root_id
                     AND d.relative_path = document_search.relative_path
                    JOIN approved_roots AS r
                      ON r.id = d.root_id AND r.workspace_id = d.workspace_id
                    WHERE document_search MATCH ?
                      AND d.workspace_id = ?
                      AND d.deleted_at IS NULL
                      AND r.revoked_at IS NULL
                    ORDER BY score ASC, d.id ASC
                    LIMIT ?
                    """,
                    (
                        _MAX_SNIPPET_TOKENS,
                        MAX_EXCERPT_CHARS,
                        expression,
                        workspace_id,
                        limit,
                    ),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "document_search" in message or "no such module: fts5" in message:
                    raise SearchIndexUnavailable(
                        "Jarvis FTS5 search index is unavailable; initialize or rebuild it"
                    ) from exc
                raise
            return [
                SearchResult(
                    document_id=str(row["document_id"]),
                    root_id=str(row["root_id"]),
                    relative_path=str(row["relative_path"]),
                    excerpt=str(row["excerpt"]),
                    score=float(row["score"]),
                )
                for row in rows
            ]

    def rebuild(self, actor_id: str, workspace_id: str) -> int:
        """Rebuild only this authorized workspace's active-document index."""
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id must be a non-empty string")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        self.database.initialize()
        with self.database._connection() as connection:
            self.database._begin(connection)
            try:
                Database._require_access(
                    connection, actor_id, workspace_id, Permission.WRITE
                )
                self.require_fts5()
                connection.execute(
                    "DELETE FROM document_search WHERE workspace_id = ?",
                    (workspace_id,),
                )
                connection.execute(
                    """
                    INSERT INTO document_search
                        (document_id, workspace_id, root_id, relative_path, extracted_text)
                    SELECT d.id, d.workspace_id, d.root_id, d.relative_path, d.extracted_text
                    FROM ingested_documents AS d
                    JOIN approved_roots AS r
                      ON r.id = d.root_id AND r.workspace_id = d.workspace_id
                    WHERE d.workspace_id = ? AND d.deleted_at IS NULL
                      AND r.revoked_at IS NULL
                    """,
                    (workspace_id,),
                )
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM document_search WHERE workspace_id = ?",
                        (workspace_id,),
                    ).fetchone()[0]
                )
                self.database._commit(connection)
                return count
            except Exception:
                self.database._rollback(connection)
                raise

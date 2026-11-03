"""Bounded, offline ingestion for explicitly approved Markdown and text roots.

The module only reads UTF-8 files. It never follows links, executes content, or
modifies source files. On Windows, Python's standard library does not expose
handle-relative directory enumeration; see ``docs/M01B_INGESTION.md`` for the
remaining race limitation of that platform fallback.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Iterator
from uuid import uuid4

from .storage import AccessDenied, Database, Permission, _now


@dataclass(frozen=True)
class SyncLimits:
    max_depth: int = 8
    max_files: int = 2000
    max_directory_entries: int = 10000
    max_file_bytes: int = 1_048_576
    max_total_bytes: int = 33_554_432


@dataclass(frozen=True)
class SyncReport:
    run_id: str
    status: str
    discovered: int
    added: int
    changed: int
    renamed: int
    deleted: int
    errors: int
    skipped: int


@dataclass
class _Snapshot:
    path: str
    text: str
    digest: str
    size: int
    mtime_ns: int
    identity: str


@dataclass
class _Scan:
    files: dict[str, _Snapshot] = field(default_factory=dict)
    errors: list[tuple[str | None, str]] = field(default_factory=list)
    skipped: list[tuple[str | None, str]] = field(default_factory=list)
    complete: bool = True
    entries: int = 0
    total_bytes: int = 0
    root_identity: tuple[str, str] | None = None


_SECRET_NAME = re.compile(
    r"(?:^\.env(?:\.|$)|(?:^|[._ -])(?:secrets?|credentials?|tokens?|password|passwd|private[_ -]?key|api[_ -]?key|id_rsa|id_ed25519)(?:[._ -]|$))",
    re.I,
)
_ALLOWED_SUFFIXES = {".md", ".txt"}


def _identity(info: os.stat_result) -> str:
    # ctime helps distinguish a recycled inode from a true rename while still
    # remaining stable across ordinary same-volume renames on supported POSIX FSes.
    return f"{info.st_dev}:{info.st_ino}:{info.st_ctime_ns}"


def _safe_name(name: str) -> bool:
    return not name.startswith(".") and not _SECRET_NAME.search(name)


def _is_utf8_name(name: str) -> bool:
    try:
        name.encode("utf-8", errors="strict")
        return True
    except UnicodeEncodeError:
        return False


def _iter_scandir(target: int | Path, result: _Scan, relative: str) -> Iterator[os.DirEntry[str]]:
    """Stream directory entries so the configured entry limit also bounds memory."""
    try:
        with os.scandir(target) as iterator:
            yield from iterator
    except OSError:
        result.complete = False
        result.errors.append((relative or None, "directory_read_error"))


def _open_root_posix(path: Path) -> int:
    """Open every root component without following a symlink or mount alias."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.scandir not in os.supports_fd
    ):
        raise OSError("required no-follow directory operations are unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open(path.anchor or "/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def _is_reparse(info: os.stat_result) -> bool:
    attrs = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attrs & flag)


def _scan_posix(root: Path, limits: SyncLimits) -> _Scan:
    result = _Scan()
    try:
        root_fd = _open_root_posix(root)
    except OSError:
        result.complete = False
        result.errors.append((None, "root_unavailable"))
        return result
    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            result.complete = False
            result.errors.append((None, "root_not_directory"))
            return result
        result.root_identity = (str(root_stat.st_dev), str(root_stat.st_ino))
        root_device = root_stat.st_dev

        def walk(directory_fd: int, relative: str, depth: int) -> None:
            if depth > limits.max_depth:
                result.complete = False
                result.errors.append((relative or None, "depth_limit"))
                return
            entries = _iter_scandir(directory_fd, result, relative)
            for entry in entries:
                result.entries += 1
                if result.entries > limits.max_directory_entries:
                    result.complete = False
                    result.errors.append((relative or None, "entry_limit"))
                    return
                name = entry.name
                if not _is_utf8_name(name):
                    result.complete = False
                    result.errors.append((None, "non_utf8_filename"))
                    continue
                rel = f"{relative}/{name}" if relative else name
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    result.complete = False
                    result.errors.append((rel, "stat_error"))
                    continue
                if stat.S_ISLNK(info.st_mode):
                    result.complete = False
                    result.skipped.append((rel, "link_skipped"))
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if info.st_dev != root_device:
                        result.complete = False
                        result.skipped.append((rel, "mounted_volume_skipped"))
                        continue
                    if not _safe_name(name):
                        result.skipped.append((rel, "excluded_name"))
                        continue
                    try:
                        child_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                    except OSError:
                        result.complete = False
                        result.errors.append((rel, "directory_open_race"))
                        continue
                    try:
                        opened = os.fstat(child_fd)
                        if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(info):
                            result.complete = False
                            result.errors.append((rel, "directory_changed"))
                        else:
                            walk(child_fd, rel, depth + 1)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    result.skipped.append((rel, "non_regular_skipped"))
                    continue
                if info.st_dev != root_device:
                    result.complete = False
                    result.skipped.append((rel, "mounted_volume_skipped"))
                    continue
                if not _safe_name(name) or Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
                    continue
                if info.st_nlink != 1:
                    result.complete = False
                    result.skipped.append((rel, "hard_link_skipped"))
                    continue
                if len(result.files) >= limits.max_files:
                    result.complete = False
                    result.errors.append((rel, "file_limit"))
                    return
                if info.st_size > limits.max_file_bytes or result.total_bytes + info.st_size > limits.max_total_bytes:
                    result.complete = False
                    result.errors.append((rel, "size_limit"))
                    continue
                try:
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_NOFOLLOW |
                        getattr(os, "O_NONBLOCK", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        before = os.fstat(file_fd)
                        if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(info):
                            raise OSError("file changed during open")
                        chunks: list[bytes] = []
                        remaining = limits.max_file_bytes + 1
                        while remaining:
                            chunk = os.read(file_fd, min(65536, remaining))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            remaining -= len(chunk)
                        raw = b"".join(chunks)
                        after = os.fstat(file_fd)
                        if (len(raw) > limits.max_file_bytes or
                                _identity(before) != _identity(after) or
                                before.st_size != after.st_size or
                                before.st_mtime_ns != after.st_mtime_ns or
                                before.st_ctime_ns != after.st_ctime_ns):
                            result.complete = False
                            result.errors.append((rel, "file_changed_or_size_limit"))
                            continue
                    finally:
                        os.close(file_fd)
                    text = raw.decode("utf-8-sig", errors="strict")
                    if "\x00" in text:
                        result.skipped.append((rel, "binary_content_skipped"))
                        result.complete = False
                        continue
                    result.total_bytes += len(raw)
                    result.files[rel] = _Snapshot(
                        rel, text, hashlib.sha256(raw).hexdigest(), len(raw),
                        after.st_mtime_ns, _identity(after)
                    )
                except UnicodeDecodeError:
                    result.complete = False
                    result.errors.append((rel, "invalid_utf8"))
                except OSError:
                    result.complete = False
                    result.errors.append((rel, "file_read_error"))

        walk(root_fd, "", 0)
    finally:
        os.close(root_fd)
    return result


def _scan_windows(root: Path, limits: SyncLimits) -> _Scan:
    """Best-effort Windows fallback using reparse checks and fd identity checks."""
    result = _Scan()
    try:
        root_info = root.stat(follow_symlinks=False)
        if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or _is_reparse(root_info):
            raise OSError("root is a reparse point")
        result.root_identity = (str(root_info.st_dev), str(root_info.st_ino))
    except OSError:
        result.complete = False
        result.errors.append((None, "root_unavailable_or_reparse"))
        return result

    def walk(folder: Path, relative: str, depth: int) -> None:
        if depth > limits.max_depth:
            result.complete = False
            result.errors.append((relative or None, "depth_limit"))
            return
        entries = _iter_scandir(folder, result, relative)
        for entry in entries:
            result.entries += 1
            if result.entries > limits.max_directory_entries:
                result.complete = False
                result.errors.append((relative or None, "entry_limit"))
                return
            rel = f"{relative}/{entry.name}" if relative else entry.name
            if not _is_utf8_name(entry.name):
                result.complete = False
                result.errors.append((None, "non_utf8_filename"))
                continue
            path = folder / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                result.complete = False
                result.errors.append((rel, "stat_error"))
                continue
            if entry.is_symlink() or _is_reparse(info) or getattr(path, "is_junction", lambda: False)():
                result.complete = False
                result.skipped.append((rel, "reparse_skipped"))
                continue
            if stat.S_ISDIR(info.st_mode):
                if _safe_name(entry.name):
                    walk(path, rel, depth + 1)
                else:
                    result.skipped.append((rel, "excluded_name"))
                continue
            if not stat.S_ISREG(info.st_mode) or not _safe_name(entry.name) or Path(entry.name).suffix.lower() not in _ALLOWED_SUFFIXES:
                continue
            if info.st_nlink != 1:
                result.complete = False
                result.skipped.append((rel, "hard_link_skipped"))
                continue
            if len(result.files) >= limits.max_files:
                result.complete = False
                result.errors.append((rel, "file_limit"))
                return
            if info.st_size > limits.max_file_bytes or result.total_bytes + info.st_size > limits.max_total_bytes:
                result.complete = False
                result.errors.append((rel, "size_limit"))
                continue
            try:
                with path.open("rb") as handle:
                    before = os.fstat(handle.fileno())
                    if _identity(before) != _identity(info) or not stat.S_ISREG(before.st_mode):
                        raise OSError("file changed during open")
                    raw = handle.read(limits.max_file_bytes + 1)
                    after = os.fstat(handle.fileno())
                if (len(raw) > limits.max_file_bytes or _identity(before) != _identity(after) or
                        before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns):
                    result.complete = False
                    result.errors.append((rel, "file_changed_or_size_limit"))
                    continue
                text = raw.decode("utf-8-sig", errors="strict")
                if "\x00" in text:
                    result.complete = False
                    result.skipped.append((rel, "binary_content_skipped"))
                    continue
                result.total_bytes += len(raw)
                result.files[rel] = _Snapshot(rel, text, hashlib.sha256(raw).hexdigest(), len(raw), after.st_mtime_ns, _identity(after))
            except UnicodeDecodeError:
                result.complete = False
                result.errors.append((rel, "invalid_utf8"))
            except OSError:
                result.complete = False
                result.errors.append((rel, "file_read_error"))

    walk(root, "", 0)
    return result


class FolderSynchronizer:
    """Synchronize one approved root into the workspace's local SQLite store."""

    def __init__(self, database: Database, limits: SyncLimits | None = None) -> None:
        self.database = database
        self.limits = limits or SyncLimits()

    def sync(self, actor_id: str, workspace_id: str, root_id: str) -> SyncReport:
        self.database.initialize()
        self.database.require_access(actor_id, workspace_id, Permission.READ)
        self.database.require_access(actor_id, workspace_id, Permission.WRITE)
        roots = self.database.list_approved_roots(actor_id, workspace_id)
        root = next((item for item in roots if item.id == root_id), None)
        if root is None:
            raise AccessDenied("workspace access denied")
        root_path = Path(root.canonical_path)
        scan = _scan_windows(root_path, self.limits) if os.name == "nt" else _scan_posix(root_path, self.limits)
        return self._persist(actor_id, workspace_id, root_id, scan)

    def _persist(self, actor_id: str, workspace_id: str, root_id: str, scan: _Scan) -> SyncReport:
        run_id = uuid4().hex
        started_at = _now()
        added = changed = renamed = deleted = 0
        status = "completed" if scan.complete else "partial"
        with self.database._connection() as connection:
            self.database._begin(connection)
            try:
                self.database._require_access(connection, actor_id, workspace_id, Permission.READ)
                self.database._require_access(connection, actor_id, workspace_id, Permission.WRITE)
                active_root = connection.execute(
                    "SELECT canonical_path FROM approved_roots WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL",
                    (root_id, workspace_id),
                ).fetchone()
                if active_root is None:
                    raise AccessDenied("workspace access denied")
                if scan.root_identity is not None:
                    current_root = Path(active_root["canonical_path"])
                    try:
                        if os.name == "nt":
                            current_stat = current_root.stat(follow_symlinks=False)
                            if current_root.is_symlink() or getattr(current_root, "is_junction", lambda: False)() or _is_reparse(current_stat):
                                raise OSError("approved root became a reparse point")
                            current_identity = (str(current_stat.st_dev), str(current_stat.st_ino))
                        else:
                            current_fd = _open_root_posix(current_root)
                            try:
                                current_stat = os.fstat(current_fd)
                                current_identity = (str(current_stat.st_dev), str(current_stat.st_ino))
                            finally:
                                os.close(current_fd)
                    except OSError as exc:
                        raise AccessDenied("approved root changed during synchronization") from exc
                    if current_identity != scan.root_identity:
                        raise AccessDenied("approved root changed during synchronization")
                    old_identity = connection.execute(
                        "SELECT device_id, file_id FROM sync_root_state WHERE root_id = ?", (root_id,)
                    ).fetchone()
                    identity = tuple(scan.root_identity)
                    if old_identity is not None and tuple(old_identity) != identity:
                        raise AccessDenied("approved root identity changed; reapprove the folder")

                connection.execute(
                    "INSERT INTO sync_runs(id, workspace_id, root_id, actor_id, status, discovered_count, changed_count, deleted_count, error_count, started_at, finished_at) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)",
                    (run_id, workspace_id, root_id, actor_id, "partial", started_at, started_at),
                )

                old_rows = connection.execute(
                    "SELECT * FROM ingested_documents WHERE root_id = ? AND workspace_id = ? AND deleted_at IS NULL",
                    (root_id, workspace_id),
                ).fetchall()
                old_by_path = {row["relative_path"]: row for row in old_rows}
                remaining_old = dict(old_by_path)
                matched_new: set[str] = set()
                rename_pairs: list[tuple[sqlite3.Row, _Snapshot]] = []
                missing = [row for row in old_rows if row["relative_path"] not in scan.files]
                new_paths = [path for path in scan.files if path not in old_by_path]
                def pair_unique(
                    old_items: list[sqlite3.Row],
                    new_items: set[str],
                    old_key: object,
                    new_key: object,
                ) -> None:
                    old_groups: dict[str, list[sqlite3.Row]] = {}
                    new_groups: dict[str, list[str]] = {}
                    for item in old_items:
                        key = old_key(item)  # type: ignore[operator]
                        if key:
                            old_groups.setdefault(str(key), []).append(item)
                    for path in new_items:
                        key = new_key(scan.files[path])  # type: ignore[operator]
                        if key:
                            new_groups.setdefault(str(key), []).append(path)
                    for key, old_group in old_groups.items():
                        new_group = new_groups.get(key, [])
                        if len(old_group) == len(new_group) == 1:
                            row, path = old_group[0], new_group[0]
                            rename_pairs.append((row, scan.files[path]))
                            matched_new.add(path)
                            remaining_old.pop(row["relative_path"], None)

                # A partial scan cannot distinguish a rename from a file that
                # was temporarily unreadable. Leave missing records untouched;
                # readable new paths are processed as additions below.
                if scan.complete:
                    pair_unique(
                        missing,
                        set(new_paths),
                        lambda row: row["file_identity"],
                        lambda snapshot: snapshot.identity,
                    )
                    unmatched_old = [row for row in missing if row["relative_path"] in remaining_old]
                    pair_unique(
                        unmatched_old,
                        set(new_paths) - matched_new,
                        lambda row: row["content_sha256"],
                        lambda snapshot: snapshot.digest,
                    )

                for old, snapshot in rename_pairs:
                    connection.execute(
                        "UPDATE ingested_documents SET relative_path = ?, content_sha256 = ?, extracted_text = ?, size_bytes = ?, mtime_ns = ?, file_identity = ?, last_seen_at = ?, deleted_at = NULL WHERE id = ?",
                        (snapshot.path, snapshot.digest, snapshot.text, snapshot.size, snapshot.mtime_ns, snapshot.identity, _now(), old["id"]),
                    )
                    self._event(connection, run_id, workspace_id, root_id, old["id"], "renamed", snapshot.path, old["relative_path"], snapshot.digest)
                    renamed += 1

                for path, snapshot in scan.files.items():
                    if path in matched_new:
                        continue
                    old = old_by_path.get(path)
                    if old is None:
                        document_id = uuid4().hex
                        connection.execute(
                            "INSERT INTO ingested_documents (id, workspace_id, root_id, relative_path, content_sha256, extracted_text, size_bytes, mtime_ns, file_identity, first_seen_at, last_seen_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                            (document_id, workspace_id, root_id, path, snapshot.digest, snapshot.text, snapshot.size, snapshot.mtime_ns, snapshot.identity, _now(), _now()),
                        )
                        self._event(connection, run_id, workspace_id, root_id, document_id, "added", path, None, snapshot.digest)
                        added += 1
                    else:
                        remaining_old.pop(path, None)
                        if old["content_sha256"] != snapshot.digest or old["file_identity"] != snapshot.identity:
                            connection.execute(
                                "UPDATE ingested_documents SET content_sha256 = ?, extracted_text = ?, size_bytes = ?, mtime_ns = ?, file_identity = ?, last_seen_at = ? WHERE id = ?",
                                (snapshot.digest, snapshot.text, snapshot.size, snapshot.mtime_ns, snapshot.identity, _now(), old["id"]),
                            )
                            self._event(connection, run_id, workspace_id, root_id, old["id"], "changed", path, None, snapshot.digest)
                            changed += 1
                        else:
                            connection.execute("UPDATE ingested_documents SET last_seen_at = ? WHERE id = ?", (_now(), old["id"]))

                # A partial scan cannot prove absence, so it never marks files deleted.
                if scan.complete:
                    for old in remaining_old.values():
                        connection.execute("UPDATE ingested_documents SET deleted_at = ?, last_seen_at = ? WHERE id = ?", (_now(), _now(), old["id"]))
                        self._event(connection, run_id, workspace_id, root_id, old["id"], "deleted", None, old["relative_path"], old["content_sha256"])
                        deleted += 1
                if scan.root_identity is not None and scan.complete:
                    connection.execute(
                        "INSERT INTO sync_root_state(root_id, device_id, file_id, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(root_id) DO UPDATE SET device_id = excluded.device_id, file_id = excluded.file_id, updated_at = excluded.updated_at",
                        (root_id, scan.root_identity[0], scan.root_identity[1], _now()),
                    )
                finished_at = _now()
                connection.execute(
                    "UPDATE sync_runs SET status = ?, discovered_count = ?, changed_count = ?, deleted_count = ?, error_count = ?, finished_at = ? WHERE id = ?",
                    (status, len(scan.files), added + changed + renamed, deleted, len(scan.errors), finished_at, run_id),
                )
                for path, code in scan.errors:
                    self._event(connection, run_id, workspace_id, root_id, None, "error", path, None, None, code)
                for path, code in scan.skipped:
                    self._event(connection, run_id, workspace_id, root_id, None, "skipped", path, None, None, code)
                self.database._insert_audit(
                    connection, workspace_id, actor_id, "source.sync.completed", "sync_run", run_id,
                    {"status": status, "discovered": str(len(scan.files)), "errors": str(len(scan.errors))},
                )
                self.database._commit(connection)
            except Exception:
                self.database._rollback(connection)
                raise
        return SyncReport(run_id, status, len(scan.files), added, changed, renamed, deleted, len(scan.errors), len(scan.skipped))

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str, workspace_id: str, root_id: str,
               document_id: str | None, event_type: str, relative_path: str | None,
               previous_path: str | None, digest: str | None, error_code: str | None = None) -> None:
        connection.execute(
            "INSERT INTO sync_events(run_id, workspace_id, root_id, document_id, event_type, relative_path, previous_path, content_sha256, error_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, workspace_id, root_id, document_id, event_type, relative_path, previous_path, digest, error_code, _now()),
        )

    def configure_auto_sync(self, actor_id: str, workspace_id: str, root_id: str,
                            enabled: bool, interval_seconds: int = 900) -> None:
        if not 60 <= interval_seconds <= 86400:
            raise ValueError("automatic sync interval must be between 60 and 86400 seconds")
        self.database.require_access(actor_id, workspace_id, Permission.WRITE)
        with self.database._connection() as connection:
            self.database._begin(connection)
            try:
                self.database._require_access(connection, actor_id, workspace_id, Permission.MANAGE_SOURCES)
                active = connection.execute(
                    "SELECT 1 FROM approved_roots WHERE id = ? AND workspace_id = ? AND revoked_at IS NULL",
                    (root_id, workspace_id),
                ).fetchone()
                if active is None:
                    raise AccessDenied("workspace access denied")
                next_due = (datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)).isoformat()
                connection.execute(
                    "INSERT INTO sync_preferences(root_id, enabled, interval_seconds, next_due_at) VALUES (?, ?, ?, ?) ON CONFLICT(root_id) DO UPDATE SET enabled = excluded.enabled, interval_seconds = excluded.interval_seconds, next_due_at = excluded.next_due_at",
                    (root_id, int(enabled), interval_seconds, next_due),
                )
                self.database._insert_audit(connection, workspace_id, actor_id, "source.auto_sync.configured", "approved_root", root_id, {"enabled": str(bool(enabled)).lower(), "interval_seconds": str(interval_seconds)})
                self.database._commit(connection)
            except Exception:
                self.database._rollback(connection)
                raise

    def run_due(self, actor_id: str, *, max_roots: int = 1, now: datetime | None = None) -> list[SyncReport]:
        """Run at most ``max_roots`` due roots; a host calls this periodically."""
        if not 1 <= max_roots <= 10:
            raise ValueError("max_roots must be between 1 and 10")
        self.database.initialize()
        current = now or datetime.now(timezone.utc)
        with self.database._connection() as connection:
            rows = connection.execute(
                "SELECT p.root_id, r.workspace_id FROM sync_preferences p JOIN approved_roots r ON r.id = p.root_id WHERE p.enabled = 1 AND r.revoked_at IS NULL AND p.next_due_at <= ? ORDER BY p.next_due_at LIMIT ?",
                (current.isoformat(), max_roots),
            ).fetchall()
        reports = []
        for row in rows:
            try:
                report = self.sync(actor_id, row["workspace_id"], row["root_id"])
            except AccessDenied:
                continue
            reports.append(report)
            with self.database._connection() as connection:
                interval = connection.execute("SELECT interval_seconds FROM sync_preferences WHERE root_id = ? AND enabled = 1", (row["root_id"],)).fetchone()
                if interval:
                    due = (current + timedelta(seconds=int(interval[0]))).isoformat()
                    connection.execute("UPDATE sync_preferences SET next_due_at = ? WHERE root_id = ? AND enabled = 1", (due, row["root_id"]))
                    connection.commit()
        return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize one approved Jarvis folder root")
    parser.add_argument("--database", required=True, help="Jarvis SQLite database path")
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--root-id", required=True)
    args = parser.parse_args(argv)
    report = FolderSynchronizer(Database(args.database)).sync(args.actor_id, args.workspace_id, args.root_id)
    print(f"{report.status}: discovered={report.discovered} added={report.added} changed={report.changed} renamed={report.renamed} deleted={report.deleted} errors={report.errors} skipped={report.skipped}")
    return 0 if report.status == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())

# M01-A — SQLite Storage and Permissions

## Database location

The default path is per-user:

- Windows: %LOCALAPPDATA%/Jarvis/jarvis.sqlite3
- Other platforms: $XDG_DATA_HOME/jarvis/jarvis.sqlite3, or ~/.local/share/jarvis/jarvis.sqlite3

Callers can provide an explicit database path for tests or controlled deployments. The application creates parent directories as needed. On POSIX systems, it attempts mode 0700 for newly created directories and mode 0600 for the database and backup. On Windows, file permissions inherit from the containing directory; this slice does not configure explicit NTFS ACLs or encrypt the database.

## Schema and migrations

M01-A introduced schema version 1, recorded with SQLite PRAGMA user_version. M01-B advances the current schema to version 2 with document, synchronization, and scheduling tables. Initialization is idempotent and applies all pending schema changes inside one BEGIN IMMEDIATE transaction. Migration statements use sqlite3 execute calls, not executescript, so a failed statement rolls back DDL and the version update together. A database created by a newer application is rejected rather than downgraded.

The current schema contains workspaces, explicit per-principal workspace permissions, approved folder roots, audit events, ingested documents, synchronization runs and events, root identity records, and bounded automatic-sync preferences. It intentionally does not create memory approval, semantic retrieval, model, or vector tables; those belong to later slices.

## Permission behavior

Workspace access is denied unless the supplied principal has the requested permission. The principal that creates a workspace receives read, write, source-management, and workspace-management grants. Only a workspace manager can grant or revoke another principal's permissions. The last manager cannot remove their own manager permission.

Approved roots must exist and be directories. The canonical path is stored. Files resolved outside that root are rejected, including traversal and symlinks that resolve outside. Revocation disables future path authorization through this API and is audit logged.

Audit events record permission and approved-root changes. Root paths and source contents are omitted from audit metadata. Audit access is restricted to workspace managers.

## Backup and restore

Use Database.backup_to(destination, actor_id) to produce a consistent SQLite online backup. The actor must manage every workspace in the database. Store backups in a protected location; a backup contains the full database.

To restore, close all application connections, then call Database.restore_from_backup(backup_path, destination). Restore checks SQLite integrity, supported schema version, required table columns, and foreign-key consistency before writing to a temporary sibling file and replacing the destination. Invalid backups are rejected before the destination is touched. Keep an independent copy of the current database before replacing it. On Windows, an open database handle may prevent replacement; stop the application and retry.

Backups are not encrypted by this slice. Deleting data from the live database does not delete older copies of backups. Retention and secure-purge policy remain for a later decision.

## Security assumptions and limitations

- This is application-level authorization. Actor IDs must come from an authenticated trusted host; they must not be accepted as authority from an untrusted client request.
- A local user who can read or modify the database file can bypass Python-level checks. OS account protection and future application packaging are part of the security boundary.
- M01-B adds bounded local file ingestion. POSIX traversal uses directory descriptors and no-follow opens; Windows rejects detected reparse points and checks identities, but Python's standard library does not provide the handle-relative directory enumeration needed to eliminate hostile concurrent path races. See [M01-B Ingestion](M01B_INGESTION.md).
- Windows root registration rejects a root recognized as a junction when the Python runtime exposes `Path.is_junction`. M01-B also rejects detected reparse points while scanning; handle-based traversal and hostile concurrent path races remain platform-specific limitations documented in [M01-B Ingestion](M01B_INGESTION.md).
- Root registration does not imply consent to any particular content policy. M01-B reads only supported text files in active approved roots and excludes common secret-like names using a heuristic.
- No database encryption, multi-user authentication service, or explicit Windows ACL management is included.
- No external packages or network calls are used by the persistence package.

## Tests

Run the standard-library suite from the repository root:

python -m unittest discover -s tests -v

Tests create temporary directories and synthetic content only.

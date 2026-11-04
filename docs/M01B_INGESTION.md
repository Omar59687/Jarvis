# M01-B: Approved Folder Ingestion

## Scope

M01-B reads UTF-8 `.md` and `.txt` files beneath an active approved root and stores extracted text and provenance in the local SQLite database. It does not create approved roots, infer consent, modify source files, approve permanent memories, or provide semantic retrieval. Cloud services and model runtimes are not used.

The synchronizer requires both workspace `read` and `write` permission for every run. It rechecks both permissions and the root's active status inside the write transaction. Revoking a root disables its automatic schedule; prior document records remain available as provenance and are not silently purged.

## Limits and exclusions

The default bounds are:

| Bound | Default |
| --- | ---: |
| Maximum directory depth below the approved root | 8 |
| Maximum supported files per run | 2,000 |
| Maximum directory entries visited per run | 10,000 |
| Maximum size per file | 1 MiB |
| Maximum aggregate file bytes per run | 32 MiB |
| Maximum automatic roots per scheduler call | 10 (default 1) |
| Automatic interval | 60 seconds to 24 hours |

All limits can be lowered with `SyncLimits`. A limit, read failure, invalid UTF-8 file, link, reparse point, mounted-volume boundary, or binary NUL marks a run partial. Partial runs do not infer deletions. Files over limits are left unchanged in the database. Only regular files with `.md` or `.txt` suffixes are considered. Hidden names and names that look like secrets (including `.env`, credentials, tokens, passwords, API keys, and common private-key names) are excluded. This is a conservative filename heuristic, not content-based secret detection; do not approve a root containing material that must never be read.

## Changes and provenance

Each document has a stable ID, workspace and root IDs, root-relative path, SHA-256 of the original bytes, extracted UTF-8 text, byte size, modification time, file identity where available, first/last seen timestamps, and optional deletion timestamp. Sync runs and events record additions, changes, renames, deletions, skips, and coarse error codes. Event records may contain root-relative filenames and hashes, but never extracted text or raw exception messages. Absolute root paths are not copied into sync events or audit metadata.

Synchronization is idempotent. A file at the same path is updated when its content or identity changes. Renames preserve the document ID when a unique file identity or unique content hash identifies the move. Ambiguous duplicate-content matches are not guessed. Deletion is soft: the prior text, hash, ID, and path remain attached to the document record with `deleted_at` set. If a scan is incomplete, deletion inference is suppressed for the whole run.

## Explicit synchronization

After the user has registered a root through the existing storage API, run:

```console
python -m jarvis_core.ingestion \
  --database /path/to/jarvis.sqlite3 \
  --actor-id alice \
  --workspace-id project-1 \
  --root-id ROOT_ID
```

On Windows, use a Windows path for `--database`. The command prints aggregate counts only. Exit status is `0` for a complete run and `2` for a partial run. Authorization and operational errors exit nonzero through Python's normal exception handling.

Python callers can use `FolderSynchronizer(Database(path)).sync(actor_id, workspace_id, root_id)`. Automatic sync is opt-in per root:

```python
synchronizer.configure_auto_sync(actor_id, workspace_id, root_id, enabled=True,
                                 interval_seconds=900)
reports = synchronizer.run_due(actor_id, max_roots=1)
```

`run_due` is deliberately a bounded scheduler tick, not a background service. The eventual desktop host may call it periodically while the application is running. The default tick handles at most one due root; each call is capped at ten. No service is installed and no process is started automatically by this package.

## Filesystem safety and limitations

On POSIX systems, traversal opens each directory and file relative to an already-open directory descriptor with no-follow flags, compares pre-open and opened identities, rejects hard-linked files and mounted-volume crossings, and checks file identity, size, modification time, and change time before and after a bounded read. The root is reopened and identity-checked before database changes commit. Symlinks and non-regular files are never read.

On Windows, [`DirEntry.stat()` returns zero for `st_dev`, `st_ino`, and `st_nlink`](https://docs.python.org/3.12/library/os.html#os.DirEntry.stat), so ingestion uses fresh `os.stat(path, follow_symlinks=False)` metadata and corroborates file identity and link count with `os.fstat()` on the opened handle. It rejects detected symlinks and junction/reparse points and skips files whose reported hard-link count is not exactly one. Windows file identity uses the volume and file IDs, not `st_ctime_ns`: Windows `st_ctime` is deprecated as a creation-time field and may differ between path and handle results. Roots or files without a stable nonzero identity are rejected or skipped. The scanner rechecks root identity before commit.

Python's standard library does not currently provide a portable handle-relative directory enumeration API for this implementation. A hostile process with write access to an approved directory could race path-based Windows enumeration/open operations. The checks reduce this risk but cannot eliminate it. For that reason, only approve folders whose contents are controlled by the user; stronger hostile-local-process protection requires a Windows handle-based native implementation and dedicated Windows testing.

The database permission layer assumes `actor_id` comes from a trusted application host. It is not an authentication system. The CLI is intended for a local trusted operator; do not expose actor IDs to an untrusted caller. SQLite itself is local persistence, not encrypted storage. OS account protections and backup handling remain important.

## Migration and recovery

M01-B advances the database schema from version 1 (M01-A) to version 2. M01-C adds schema version 3 for FTS5 lexical retrieval; see M01C_RETRIEVAL.md. Migrations are applied transactionally by Database.initialize() and can be called repeatedly. A failed migration rolls back the schema changes and version update.

The database location is provided by `default_database_path()` and documented in [M01-A Storage](M01A_STORAGE.md). Before upgrades or restore operations, close the Jarvis process and create a verified backup using the storage API. Restore uses a temporary sibling database, runs SQLite integrity and schema checks, and replaces the destination only after validation succeeds. Keep backups private: document text is stored in clear text inside SQLite.

## Test data policy

Tests use only generated files in temporary directories. No home directory, user-selected root, or real personal folder is scanned. Windows junction tests run only on Windows and may skip when junction creation is unavailable; Linux results do not validate the Windows race limitation described above.

## Validation snapshot

Historical M01-B baseline: on Linux x86_64 with Python 3.12.14 and SQLite 3.53.1, the suite had 41 tests: 35 passed and six Windows-only tests skipped. The exercised commands were `python -m py_compile jarvis_core/*.py tests/*.py`, `python -m jarvis_core.ingestion --help`, and `python -m unittest discover -s tests -v`. This historical M01-B run predates the M01-E acceptance additions and is not a current full-suite result.

## Windows verification checklist

The project maintainer reports that the Windows checklist was completed for the merged M01-A through M01-E baseline, including ordinary files, metadata identity, hard links where supported, junctions, symlinks, root replacement, migrations, and regressions. The individual Windows logs are not included in this repository snapshot. Re-run the commands below when validating a new Windows build or later changes:

- `py -3.12 -m py_compile jarvis_core/*.py tests/*.py` and `py -3.12 -m jarvis_core.ingestion --help`.
- `py -3.12 -m unittest discover -s tests -p test_ingestion.py -v`; check ordinary files, path/handle IDs, junction, symlink, hard-link (where supported), and root-replacement cases.
- `py -3.12 -m unittest discover -s tests -p test_storage.py -v`; ensure symlink tests run where permitted.
- `py -3.12 -m unittest discover -s tests -v` for the full regression suite.

The automated Windows root-replacement case swaps the approved directory after scanning and before persistence. It validates identity rechecking but does not simulate every possible concurrent junction race; the limitation above remains. M01-E added cross-service integration and restore target-open failure coverage; the maintainer reports the merged M01-A through M01-E baseline was Windows-verified.

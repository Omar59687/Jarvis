# M04 — Persistent Conversational Memory

## Confirmed scope

- Persistent conversation memory is optional, local-first, and scoped to the authenticated Jarvis principal and one currently authorized workspace.
- Consent must be explicitly enabled in the dashboard. Persistence is off when no consent row exists and remains off after migration. Existing session history is never copied during opt-in.
- Text messages and recognized voice transcripts use the authenticated chat endpoint and the same persistence path. Raw microphone audio is not stored by this feature.
- Saved conversation text is untrusted context. It is not workspace evidence, a citation source, or an instruction source. Retrieval is bounded to six turns and 3 KiB of serialized UTF-8 context.

## Implemented consent and authorization

- The dashboard shows the setting only after an authenticated workspace is selected. Enabling requires current workspace `WRITE`; reading and deleting saved history, and revoking consent, require `READ`. The server repeats these checks for every operation; UI state is not authority.
- Consent is per actor and workspace. Other users and workspaces cannot list, inspect, retrieve, append to, or delete the conversation rows through the service APIs.
- Only future completed text turns and recognized voice transcripts are eligible for storage after opt-in. If workspace `WRITE` is lost while consent remains enabled, new turns are kept in the current process session only; the dashboard explains this and the user may revoke consent with `READ` access.
- Revocation deletes that actor/workspace's saved conversation rows, increments the consent revision, and clears matching process-local contexts. A stale in-flight response cannot append after revocation. Re-enabling starts with future turns only.
- Deleting one saved conversation removes its rows from the active SQLite database and clears matching process-local session context. The audit event stores deletion metadata, not conversation text.

## Implemented retention

- New settings default to 30 days. The service accepts integer retention periods from 1 through 365 days; the dashboard offers 7, 30, 90, 180, and 365 days.
- The selected retention applies to each new turn. Older turns in a continuing conversation can therefore expire before newer turns.
- Expired turns are excluded from retrieval immediately and physically purged during database access or by the service's five-minute local sweep. Purging is limited to the configured trusted actor.
- SQLite backup files made before deletion or expiry may retain older copies. Backup retention and secure backup erasure remain operational responsibilities; this change does not encrypt or rewrite prior backups.

## Implemented retrieval and privacy boundaries

- The service ranks at most 120 recent eligible turns by lexical overlap, selects up to six within a 3 KiB context budget, and restores chronological order before prompting. This is simple keyword retrieval, not semantic search.
- The prompt labels serialized session and saved turns as untrusted user data, separate from trusted system/developer instructions. Workspace factual claims remain governed by existing RAG evidence and citation validation.
- Durable rows are stored in the existing SQLite database. This change does not add browser transcript storage, raw-audio storage, a cloud memory service, or a new dependency.
- Existing process-local context remains available while persistence is off, within its prior session/workspace and size limits.

## Open limitations

- SQLite content is not newly encrypted at rest by this milestone. Existing filesystem/database protection remains in effect.
- Deletion and revocation remove content from the active database, but cannot erase historical copies in backups or filesystem snapshots.
- Lexical relevance is intentionally basic and can miss paraphrases, especially across English and Arabic. Language-aware retrieval evaluation is future work.
- Workspace-switch behavior is protected by server session-generation checks and workspace-scoped queries, but still needs broader live Windows acceptance.

## Evidence

Focused coverage is in `tests/test_m04_persistent_conversation.py`. On 2026-11-22, `python -m unittest discover -s tests -v` ran 348 tests: 339 passed and 9 had platform/privilege skips. `node --test tests/*.test.mjs` passed 24 tests. `python -m compileall -q jarvis_core prototypes tests`, `node --check prototypes/m03_b1/app.js`, and `git diff --check` passed. Tests use temporary SQLite databases and synthetic providers; they do not modify the personal database or require a live model.

# M08-C01.1 — Proactivity Database Foundation

**Status:** C01.1 database foundation is implemented as part of M08-C. It
introduced schema v17; C01 follow-on work advances the current SQLite schema
to v18. Schema v17 is the previous version for the v18 migration. See the C01.1 decision in
[DECISIONS.md](DECISIONS.md).

## Tables

- `proactivity_preferences` is keyed by workspace and principal. Its `enabled`
  and generation defaults are zero. It stores bounded check interval and
  cooldown values plus creation and update timestamps.
- `proactive_suggestions` stores a unique ID, fixed type and lifecycle state,
  workspace/principal ownership, scoped deduplication digest, task or mission
  source reference, source revision digest, timestamps, and optional snooze
  timestamp. It stores no mission goal, source summary, model response, or
  arbitrary content.
- `proactivity_suggestion_events` stores scoped transition vocabulary, bounded
  JSON object metadata, and timestamp. Events reject updates and can be deleted
  only during a task purge covered by the existing purge tombstone.

## Enforced limits and service obligations

SQLite enforces preference interval 900–86400 seconds, cooldown 0–2592000
seconds, fixed suggestion types and states, fixed event vocabulary, digest
shape, workspace/principal uniqueness, task/mission source consistency,
foreign keys, JSON object metadata up to 4096 bytes, and an expiration time
after creation no more than 30 days later. C01.1 alone did not provide
service-level enforcement; the later M08-C service enforces authorization,
generation fencing, cooldown semantics, timestamp construction, and the active
suggestion limit. Metadata size does not establish semantic privacy.

## Migration and deletion

Migration v17 is additive and runs in the existing `BEGIN IMMEDIATE` chain.
Fresh initialization and upgrade from v16 use the same migration. Migration
v18 adds the fixed reason code and durable scheduler claim/lease table and is
additive from v17. Backup restore validation checks the schema corresponding
to the backed-up version, append-only event protections, integrity, and foreign
keys. Existing manager-confirmed task purge writes a tombstone before deleting
tasks; dependent suggestions and their events cascade under that authorized
flow. Historical backup files are not rewritten or erased.

M08-C adds a bounded service and authenticated dashboard over these tables.
There is no model inference, autonomous mission action, or execution authority.

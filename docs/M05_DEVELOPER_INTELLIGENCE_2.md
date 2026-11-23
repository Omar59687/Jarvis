# M05 Developer Intelligence 2.0

## Implemented scope

- Repository scans remain explicit, read-only, and bound to the commit approved in the trusted terminal. Existing approved-root, `MANAGE_SOURCES`, workspace `READ`, and snapshot rechecks remain authoritative.
- Indexing reuses unchanged Git blobs, removes paths absent from a complete new tree, and reports added, modified, deleted, uniquely identical-blob renamed, reused, and indexed-file counts. Ambiguous identical-blob moves remain add/delete. A failed or malformed tree read aborts before the transaction, preserving the previous snapshot. Oversized indexable content fails the scan instead of silently dropping that path.
- Schema v13 adds bounded reference metadata. Existing repositories migrate as stale and files as `unknown`; re-indexing is required before reference metadata is available. Backups at v13 must include the new column; older supported backups continue through their matching migration path.
- Python uses `ast` for definitions, imports, loaded-name references, and call candidates. JavaScript/TypeScript declarations and references use bounded lexical recognition and are always `partial`; references can be missed or misclassified. Dart, C#, SQL, and Markdown keep their existing partial metadata and do not claim call/reference completeness. Other languages receive content search only when their extension is already indexable.
- Search ranks exact symbol definitions above references, then exact path matches, then existing FTS relevance. Weak filename substrings do not outrank content relevance. Ordering remains deterministic.
- The graph adds symbol-reference candidates, Python call candidates when the caller and target each resolve uniquely, and dependency edges for resolved local imports. Ambiguous calls are omitted or represented as `call_candidate`; call edges carry parser confidence. Cycles are bounded and labeled as candidates when based on calls. No graph result proves runtime behavior or completeness.
- Developer Q&A reuses the existing bounded search/source/citation path. Caller files and definitions can both be supplied as evidence; retrieved code remains untrusted and factual claims still require validated server-issued citations.

## Limits and operations

The index retains the existing per-repository file/byte limits and current-snapshot model. Metadata is capped per file. Graph output is limited to 500 files, 1,200 nodes, and at most three times the caller-selected node limit in edges; cycle reporting is capped at 20 components. Definitions/references are static candidates, not compiler or language-server semantics. Dynamic dispatch, aliases, reflection, macros, generated code, framework behavior, and cross-language calls are not resolved. Repository code is never executed.

After upgrading from schema v12, explicitly re-index each repository from the trusted terminal to populate the new metadata. Search, source, graph, and Q&A continue to enforce existing workspace authorization and stale-snapshot checks. No new external runtime or paid service is introduced.

## CI

`.github/workflows/tests.yml` runs the Python unittest suite, Python compilation, Node tests, and dashboard syntax validation on GitHub-hosted Ubuntu and Windows runners. A Linux result does not establish Windows path/junction behavior; skipped platform tests remain visible in runner output. CI does not download models or run hardware-dependent voice acceptance.

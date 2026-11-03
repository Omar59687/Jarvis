# M01-C: Bilingual Knowledge Retrieval

## Status and scope

M01-C adds local lexical search over UTF-8 Markdown and text already ingested by M01-B. It uses SQLite FTS5 and the Python standard library. Every search requires an explicit actor and workspace ID and checks that actor's workspace read permission, including for empty queries. Results contain a ranked document ID, active root ID, root-relative path, bounded plain-text excerpt, and lexical score.

Search excludes soft-deleted documents and documents whose approved root has been revoked. A root revocation removes its documents from the active FTS index; search also rechecks the source tables before returning a match. The slice does not search approved memories, pending candidates, or user-entered facts, and it never creates permanent memories.

## Schema and index lifecycle

M01-C advances SQLite schema version 2 to version 3. The transaction creates an FTS5 table, indexes eligible existing documents, and installs triggers for document insert/update/delete and root revocation/deletion. Changed text and renamed paths are reflected by existing M01-B writes through these triggers. The document update trigger only runs for search-relevant fields and skips updates whose indexed values did not change, so routine last-seen timestamps do not rewrite FTS entries. Soft deletion removes text from the active search index.

SearchIndex.rebuild(actor_id, workspace_id) rebuilds only that authorized workspace's active source documents and requires write permission. Repeated rebuilds are idempotent. A schema v3 SQLite backup contains the FTS5 table. A schema v2 backup remains restorable and Database.initialize() upgrades it before search.

The application probes the SQLite runtime for FTS5 before search. Search raises FTS5Unavailable with a clear message when FTS5 is absent. Schema migration also reports that schema v3 requires SQLite built with FTS5. No extension is downloaded or loaded.

## Query and result behavior

Python callers use `SearchIndex(Database(path)).search(actor_id, workspace_id, query, limit=10)`; the trusted host supplies actor and workspace identities.

- Queries are limited to 512 Unicode code points; the result limit must be an integer from 1 to 20.
- The workspace permission check occurs before returning an empty result for blank, punctuation-only, or mark-only input.
- Embedded NULs and invalid surrogate code points are rejected with ValueError.
- Up to 32 distinct Unicode letter and number terms are extracted. Terms are quoted as FTS5 phrases and combined with OR. SQL values, including the FTS expression and limit, use bound parameters. SQL operators and punctuation in input are treated as separators, not executable syntax.
- Results order by ascending FTS5 BM25 score, then document ID for deterministic ties. A lower score is a stronger lexical match.
- FTS5 supplies a context excerpt capped at 400 Unicode code points. Excerpts contain untrusted source text; consumers must render them as text, not HTML.

## Linguistic limits

FTS5 unicode61 is a Unicode tokenizer, not Arabic language processing. It does not stem Arabic, segment clitics, correct spelling, translate, transliterate, or match concepts. Inflected Arabic forms may not match. English stemming and synonym expansion are also absent. Diacritics and Arabic presentation forms may tokenize differently; the query path applies NFKC, while indexed source text is retained as ingested.

Arabic queries match Arabic tokens and English queries match English tokens. The system does not translate queries or perform Arabic-English cross-language retrieval. Search quality depends on literal token overlap and has not been benchmarked against human relevance judgments.

## Limitations and security assumptions

- FTS5 support depends on the Python/SQLite build, including Windows distributions; check fts5_available() in the deployed interpreter.
- Ranking is lexical and is not a truth or answer-quality score.
- The FTS index and excerpts are unencrypted, like the SQLite database; OS account protections apply.
- The actor ID must come from a trusted host. This service performs authorization but is not an authentication system.
- M01-B filesystem race limitations remain unchanged.
- Windows compatibility has not been verified unless the test suite runs on Windows.

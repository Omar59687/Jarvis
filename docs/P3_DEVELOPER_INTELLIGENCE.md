# P3 Developer Intelligence — first usable slice

**Status:** this branch extends the bounded read-only P3 slice from `feature/p2-closure-p3-developer-intelligence`. It adds partial multi-language extraction, a commit-scoped repository graph, bounded Git change evidence, and connected graph/change UI. It is not a complete semantic code graph or a production coding agent.

## Implemented flow

1. A trusted local operator first adds an approved source root to a Jarvis workspace using the existing M01 permission workflow.
2. A principal with `MANAGE_SOURCES` explicitly registers a Git repository under that approved root from an interactive terminal. The repository does not register through the browser. Registration checks the root grant, path containment, and link/junction components.
3. The operator explicitly starts indexing from that terminal. Indexing is read-only and commit-pinned. It uses Git object commands, with no shell, hooks, checkout, build, test, package installation, or repository-code execution. It rejects linked worktrees, Git config includes, and external object alternates; inherited `GIT_*` environment overrides are not passed to Git.
4. Indexed file records and a separate FTS5 index are scoped to a workspace and repository. A changed commit is marked stale and cannot be searched/cited until re-indexed. Unchanged blobs reuse their indexed rows; only changed tracked blobs are reparsed. Schema v7 records parser status; schema v13 adds bounded reference metadata and marks existing snapshots stale for explicit re-indexing.
5. The service derives a bounded graph from the active workspace/repository/commit files: repository, directory, file, symbol, and module nodes; contains, defines, imports, local dependency, and static reference/call-candidate edges. Every edge carries confidence metadata. Non-Python lexical imports remain `lexical_partial` module hints and are not turned into file-to-file references. Python call edges require unique indexed caller and callee symbols; ambiguous calls remain candidates or are omitted. Every response is tied to the current indexed commit. The graph is materialized per request from authorized indexed rows; it is not a global graph database.
6. Commit selectors list at most 20 recent ancestors of the current indexed HEAD. The comparison service accepts only commits from the repository’s most recent bounded 50-commit history and returns bounded added/modified/deleted/type-changed file evidence. The diff command uses literal pathspecs, disables external diff/textconv, rejects unapproved paths and symlinks, and filters obvious secret material. Deleted-file citations use the deleted file's old-side line range; other evidence line ranges refer to the new side. For current-HEAD Python files with complete AST metadata, line-overlap symbol hints are labeled as such; they are not a claim that a symbol was semantically changed.
7. The authenticated browser can list repositories for its currently selected workspace, search indexed files and symbols, inspect bounded source lines, navigate the graph, compare commits, and ask one single-turn local-provider question. The service rechecks READ access and repository commit before and after generation. Optional selected commit diffs can be included as evidence. Chat is ephemeral and uses the configured existing provider; no direct browser-to-model request exists.

Repository source and documentation text are untrusted. The model receives only a bounded set of matched excerpts with `R#` labels and no tools. It must cite exact server-issued labels. Unknown/missing citations, incomplete generations, no-evidence queries, stale indexes, provider failures, permission revocation, and unavailable repositories fail closed. Source cards expose repository ID/name, commit, relative path, line range, and a bounded excerpt, never an absolute path.

## Registration and indexing commands

Run these commands from a trusted PowerShell window and replace the placeholders. The database and selected workspace must already exist; the principal must have `MANAGE_SOURCES` access and the repository must be beneath an active approved root. Registration prints the repository ID and the exact commit observed. Before indexing, Jarvis prints the registered identity, path relative to the approved root, current commit, and branch. The operator must type `INDEX <full-commit-sha>` to approve that exact snapshot; if HEAD changes before indexing begins, indexing fails closed. Dirty and untracked working-tree files are never read.

Verify an approved Git CLI is installed with `git --version`. Install/upgrade Git only through the owner's normal software process; Jarvis does not install Git or invoke package managers.

```powershell
$db = "$env:LOCALAPPDATA\Jarvis\jarvis.sqlite3"
py -3.14 -m jarvis_core.developer_cli --database $db register `
  --actor-id "<existing-principal-id>" `
  --workspace-id "<workspace-id>" `
  --approved-root-id "<approved-root-id>" `
  --path "C:\AI Projects\<repository>"
```

The CLI displays the supplied path and workspace and requires typing `REGISTER`. It accepts only an interactive standard input. Record the printed repository ID, then explicitly index it:

```powershell
py -3.14 -m jarvis_core.developer_cli --database $db index `
  --actor-id "<existing-principal-id>" `
  --workspace-id "<workspace-id>" `
  --repository-id "<repository-id>"
```

Review the displayed repository and commit, then type the exact `INDEX <full-commit-sha>` confirmation shown by the terminal. No code is run. Open the Jarvis dashboard, pair, select the same workspace, then use **Developer Intelligence** to select the repository, search a symbol/file, and ask a question. Each result should name the indexed commit and line range. Change the HEAD commit and retry a query to confirm the UI reports a stale index; re-index only after inspecting the new commit in the trusted terminal.

Start the local service using a model provider/model that the owner already selected and installed. For example, with LM Studio configured as local-only and its native server bound to loopback:

```powershell
py -3.14 -m prototypes.m03_b1.service `
  --port 8765 `
  --provider lmstudio `
  --model "<explicit-installed-model-id>" `
  --base-url http://127.0.0.1:1235 `
  --timeout 120 `
  --actor-id "<existing-principal-id>" `
  --database $db
```

Open `http://127.0.0.1:8765/`, pair with the terminal code, and select the workspace where the repository is registered. Keep the transcript/source panel in memory only; reload the page to clear it. No live Windows or real-model test is claimed by this branch's offline results.

## Index scope and extraction limits

- File contents supported for lexical search: `.py`, `.dart`, `.cs`, `.js`, `.jsx`, `.ts`, `.tsx`, `.sql`, and `.md`.
- Python classes, functions, async functions, and imports are extracted with Python `ast`; syntax failures yield no symbols/imports and `analysis_status=failed`. The AST summary remains bounded to 100 symbols and 100 imports per file.
- Dart and C#/JavaScript/TypeScript use small standard-library lexical recognizers for common imports and declaration signatures; SQL recognizes common `CREATE` declarations and `FROM`/`JOIN`/`REFERENCES`; Markdown recognizes headings and local links. These results carry `confidence=lexical_partial` and `analysis_status=partial`. They are hints, not complete parse trees. Comments and common string literals are masked before declaration extraction; unusual syntax can be missed or misclassified.
- Graph edges include a `confidence` field. `contains` edges reflect the indexed tree; Python AST and partial lexical declarations/imports carry their corresponding parser confidence. File-to-file `references` are emitted only for a Python AST import or explicit Markdown local link that resolves to exactly one indexed file; non-Python lexical imports remain partial module hints. A module node may still exist when the dependency is external or resolution is unknown. C# namespace-to-file mapping and SQL object-to-source mapping are not guessed.
- The index reads tracked Git blobs from `HEAD`; untracked or dirty working-tree edits are not indexed. A failed or malformed tree scan aborts before replacing the prior snapshot. Citations refer to one exact commit.
- `.git`, dotfiles (including `.env`), common secret/key filename patterns, `node_modules`, virtual environments, build/vendor/artifact directories, submodules, and Git symlink blobs are excluded. A bounded content screen skips obvious private-key and common token/credential material. This screen is heuristic; it cannot detect every secret embedded in ordinary source.
- Limits are 25 registered repositories/workspace, 10,000 tracked tree entries, 1 MiB per file, 32 MiB indexed text/workspace, 10 search results, 5 evidence excerpts/question, 1,400 excerpt characters/evidence, 200 lines/source view, 500 graph files, 1,200 graph nodes, at most three times the selected graph-node limit in edges, 20 cycle components, 50 eligible history commits, and 100 diff paths/request. Graph/diff process calls have finite per-process time/output caps; diff detail has an overall local time budget and can return `truncated=true`.
- Indexing is incremental for unchanged blob IDs but replaces the one current repository snapshot; prior source snapshots are not retained. The index summary reports added/modified/deleted/uniquely-identical-blob renamed files; ambiguous moves remain additions/deletions. Change views compare only commits reachable from the indexed current HEAD and within the bounded history window. Diff line ranges are factual; symbol hints are AST line-overlap hints, not a semantic change classification. Static call/dependency candidates do not establish runtime behavior or completeness.
- The API excludes dotfiles and dot-directories, generated/vendor directories, common secret-name patterns, Git symlinks/submodules, and source whose bounded content scan detects common private-key/token/credential patterns. These filters are heuristic and do not replace careful repository selection or a dedicated secret scanner.

## API surface

All operations use the existing same-origin local service and the selected workspace from the paired session. The browser does not send an actor or workspace authority for these calls.

| Route | Purpose |
|---|---|
| `GET /api/v1/developer/repositories` | List authorized repository records for the active workspace. |
| `GET /api/v1/developer/repositories/{id}/graph?limit=300` | Return bounded graph nodes/edges for the current indexed commit, plus `truncated`. |
| `GET /api/v1/developer/repositories/{id}/commits` | Return at most 20 safe commit selectors from the repository’s recent history. |
| `GET /api/v1/developer/repositories/{id}/changes?base={sha}&target={sha}` | Compare only eligible commit SHAs and return safe changed-file evidence. `target` may be omitted to select indexed HEAD. |
| `GET /api/v1/developer/repositories/{id}/files?q=...` | Bounded FTS search over the indexed current snapshot. |
| `GET /api/v1/developer/repositories/{id}/files/{file_id}` | Return a bounded source-line window without an absolute path. |
| `POST /api/v1/developer/chat` | Single-turn local-provider answer; may include a selected `{comparison_base, comparison_target}` pair. Citations are server-issued. |

## P3 requirement status

| Requirement | Status in this slice | Boundary |
|---|---|---|
| P3.1 Authorized registration | Implemented | Explicit terminal confirmation, existing approved root and `MANAGE_SOURCES`; no browser registration or arbitrary disk scan. |
| P3.2 Multi-language indexing | Partial | Python AST plus bounded lexical metadata for Dart, C#, JavaScript/TypeScript, SQL, and Markdown. Only Python AST results are marked complete; the other extractors are explicitly partial. |
| P3.3 Developer graph | Implemented as a bounded derived graph | Workspace/repository/current-commit nodes and relationships; local references appear only when uniquely resolved. No global graph table, semantic dependency/call graph, test mapping, or runtime behavior graph. |
| P3.4 Git change evidence | Implemented with limits | Added/modified/deleted/type-change diff hunks, recent commit selection, current-HEAD Python AST line-overlap hints, and optional comparison evidence in Q&A. Renames appear delete-plus-add; historical symbol changes are not classified. |
| P3.5 Questions | Partial | Single-turn local-provider answers can include selected diff evidence. Lexical retrieval remains the selection method; broad architecture/dependency questions may abstain. |
| P3.6 UI | Implemented as a first dashboard | SVG graph with node highlighting plus an accessible keyboard node list, commit selectors, comparison view, source search, Q&A/citations, English/Arabic strings, RTL, and safe text rendering. No server registration UI or automated Arabic/RTL owner acceptance. |
| P3.7 Regression/security | Implemented for the declared slice | Synthetic tests cover auth/isolation, Git CLI argument injection, malicious config, linked worktrees, detached HEAD, path/link/reparse-point/secret exclusions, parser failures, graph scope, changes, stale commits, citation validation, injection framing, migration rollback/backup, operator commit confirmation, dirty-tree exclusion, replacement, and P2 API compatibility. Target-Windows filesystem race behavior remains unverified. |

## Windows acceptance boundary

The repository path guard rejects symbolic links, junctions, and entries carrying the Windows `FILE_ATTRIBUTE_REPARSE_POINT` flag. Linux tests simulate that attribute; they do not prove Windows handle identity, junction, case-folding, drive-boundary, CRLF, or SQLite lock behavior. Do not treat P3 as Windows-accepted until the owner runs the focused suite on the target Windows/Python/Git versions and performs the trusted-terminal workflow above. A locked database is reported without printing its path or SQLite internals; close the Jarvis process using the database and retry.

## Migration and dependencies

Schema v6 adds `developer_repositories`, `developer_files`, a workspace/repository/commit-bound FTS5 index, and synchronization triggers. Schema v7 adds a checked `analysis_status` column. Both migrations are transactional; failed v5→v6 and v6→v7 migration tests verify rollback, and current backup/restore tests verify the indexed metadata. Existing P2 memory/document tables and authorization are unchanged. No Python or JavaScript package was added. P3 requires a local Git CLI; Jarvis does not bundle it. Git commands use a minimal environment, fixed arguments, bounded output/time, no shell, disabled external diff/text conversion, and no network operation or lazy fetch.

## Security assumptions and remaining risks

- Registration authority comes from existing `MANAGE_SOURCES`; read, search, source view, and question endpoints require the active paired session and workspace READ permission. Client-supplied actor/workspace identity is rejected or ignored as authority.
- Repository identity and files are always selected with repository ID plus workspace ID and active approved root. Foreign-workspace IDs do not reveal file content.
- Git configuration includes, alternates, linked worktree metadata, tracked symlink blobs, and paths outside the approved root are rejected/skipped. The Git CLI and database are in-process local trust dependencies. A malicious same-user process racing path replacement or modifying the database/configuration remains outside this local-alpha threat boundary; Windows directory handles do not provide a fully race-free cross-platform walk in this implementation.
- Filename/content and diff secret exclusion is best effort. Review whether source code that contains non-obvious secrets should be registered at all; no comprehensive secret scanner is claimed.
- FTS is lexical; non-Python metadata is heuristic and marked partial. Citations establish source/diff location rather than semantic entailment. Do not treat the graph as a runtime graph or an answer as runtime verification.
- No code editing, shell tools, agent execution, file mutation, dependency installation, deployment, persistent chat, cloud inference, or automatic memory write is included.

## Focused tests

`tests/test_developer_intelligence.py` covers registration approval, permission checks, root containment, symlinks/worktrees, Git config include rejection, secret and link exclusion, multi-language partial extraction, malformed Python, graph nodes/edges and scoping, authorization rechecks during graph reads, literal-path commit diffs and deleted-file line ranges, commit argument injection, changed-file/symbol hints, interrupted indexing atomicity, stale commits, revocation before/during generation, cross-workspace denial, citation validation, provider completion, v5→v6/v6→v7 migration rollback, and backup/restore. `tests/test_p3_acceptance.py` runs a disposable multi-language repository through trusted-terminal registration, exact-commit indexing, search, source-line retrieval, graph links, history comparison, dirty-tree exclusion, head-race rejection, repository replacement, reparse-point rejection, and locked-database error handling. Service integration uses the paired local session, selected workspace, CSRF header, synthetic Git repository, and deterministic fake provider; it does not verify real provider quality.

Run the focused and full offline checks in PowerShell:

```powershell
py -3.14 -m unittest tests.test_p3_acceptance tests.test_developer_intelligence -v
py -3.14 -m unittest tests.test_m03_p2a_chat tests.test_m03_p2a_identity tests.test_coding_agents -v
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q jarvis_core prototypes eval tests
node --check prototypes/m03_b1/app.js
node --check prototypes/m03_b1/chat-ui-state.mjs
node --test tests/chat_ui_state.test.mjs tests/m03_a_galaxy.test.mjs
git diff --check
```

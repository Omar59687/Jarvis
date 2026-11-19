# P3 Developer Intelligence — first usable slice

**Status:** implemented as a bounded read-only vertical slice on `feature/p2-closure-p3-developer-intelligence`. This is not a complete semantic code graph or a production developer agent.

## Implemented flow

1. A trusted local operator first adds an approved source root to a Jarvis workspace using the existing M01 permission workflow.
2. A principal with `MANAGE_SOURCES` explicitly registers a Git repository under that approved root from an interactive terminal. The repository does not register through the browser. Registration checks the root grant, path containment, and link/junction components.
3. The operator explicitly starts indexing from that terminal. Indexing is read-only and commit-pinned. It uses Git object commands, with no shell, hooks, checkout, build, test, package installation, or repository-code execution. It rejects linked worktrees, Git config includes, and external object alternates; inherited `GIT_*` environment overrides are not passed to Git.
4. Indexed file records and a separate FTS5 index are scoped to a workspace and repository. A changed commit is marked stale and cannot be searched/cited until re-indexed. Unchanged blobs reuse their indexed rows; only changed tracked blobs are reparsed.
5. The authenticated browser can list repositories for its currently selected workspace, search indexed files, inspect bounded source lines, and ask one single-turn local-provider question. The service rechecks READ access and the repository commit before and after generation. Chat is ephemeral and uses the configured existing provider; no direct browser-to-model request exists.

Repository source and documentation text are untrusted. The model receives only a bounded set of matched excerpts with `R#` labels and no tools. It must cite exact server-issued labels. Unknown/missing citations, incomplete generations, no-evidence queries, stale indexes, provider failures, permission revocation, and unavailable repositories fail closed. Source cards expose repository ID/name, commit, relative path, line range, and a bounded excerpt, never an absolute path.

## Registration and indexing commands

Run these commands from a trusted PowerShell window and replace the placeholders. The database and selected workspace must already exist; the principal must have `MANAGE_SOURCES` access and the repository must be beneath an active approved root.

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

Type `INDEX` to confirm that the current Git commit will be read. No code is run. Open the Jarvis dashboard, pair, select the same workspace, then use **Developer Intelligence** to select the repository, search a symbol/file, and ask a question. Each result should name the indexed commit and line range. Change the HEAD commit and retry a query to confirm the UI reports a stale index; re-index only after inspecting the new commit in the trusted terminal.

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
- Python classes, functions, async functions, and imports are extracted with Python `ast`. Syntax failures yield no symbols/imports, not a guessed parse.
- For Dart, C#, JavaScript/TypeScript, SQL, and Markdown, indexed language/path/content is available but symbols and relationships are reported unknown. No regex output is presented as a complete parser.
- The index reads tracked Git blobs from `HEAD`; untracked or dirty working-tree edits are not indexed. Citations refer to one exact commit.
- `.git`, dotfiles (including `.env`), common secret/key filename patterns, `node_modules`, virtual environments, build/vendor/artifact directories, submodules, and Git symlink blobs are excluded. A bounded content screen skips obvious private-key and common token/credential material. This screen is heuristic; it cannot detect every secret embedded in ordinary source.
- Limits are 25 registered repositories/workspace, 10,000 tracked tree entries, 1 MiB per file, 32 MiB indexed text/workspace, 10 search results, 5 evidence excerpts/question, 1,400 excerpt characters/evidence, and 200 lines/source view.
- Indexing is incremental for unchanged blob IDs but it currently replaces the one current repository snapshot; prior source snapshots are not retained. Commit diff comparison, full dependency graphs, call graphs, framework semantics, and comprehensive parsers are not implemented.

## P3 requirement status

| Requirement | Status in this slice | Boundary |
|---|---|---|
| P3.1 Authorized registration | Implemented | Explicit terminal confirmation, existing approved root and `MANAGE_SOURCES`; no browser registration or arbitrary disk scan. |
| P3.2 Indexing and structure | Partial | Bounded incremental Git blob indexing; Python AST metadata; other supported languages are searchable but symbols/imports are unknown. |
| P3.3 Developer knowledge model | Partial | Workspace/repository/commit/file records and Python symbol/import metadata; no directory/symbol graph, commit diff, test-to-component mapping, or semantic relationships. |
| P3.4 Commit evidence | Implemented for indexed answers | Citations bind to server-selected workspace/repository, exact current commit, relative path, evidence type and line range; stale snapshots fail closed. |
| P3.5 Questions | Partial | Single-turn lexical-evidence Q&A through the configured local provider; no evidence yields abstention. Broad architecture/dependency questions may have no lexical matches and are not answered by inferred graphs. |
| P3.6 UI | Partial | Bilingual repository selector, file search, bounded source view, question panel and citation cards; no repository graph, overview page, or registration UI. |
| P3.7 Regression/security | Implemented for this slice | Synthetic repositories cover authorization, path/link/config rejection, exclusions, size limits, parser failure, stale commits, citations, hostile instructions, provider failure, migration and API session/CSRF. Windows-specific reparse-point race testing remains unverified. |

## Migration and dependencies

Schema v6 adds `developer_repositories`, `developer_files`, a workspace/repository/commit-bound FTS5 index, and synchronization triggers. Upgrade is transactional; rollback and v5 backup/restore behavior are tested. Existing P2 memory and document tables and access rules are unchanged. No Python or JavaScript package was added. P3 requires a local Git CLI installation; Jarvis does not bundle it. Git commands are fixed read-only subcommands with a minimal environment, bounded stdout/time, no shell, and no network operation.

## Security assumptions and remaining risks

- Registration authority comes from existing `MANAGE_SOURCES`; read, search, source view, and question endpoints require the active paired session and workspace READ permission. Client-supplied actor/workspace identity is rejected or ignored as authority.
- Repository identity and files are always selected with repository ID plus workspace ID and active approved root. Foreign-workspace IDs do not reveal file content.
- Git configuration includes, alternates, linked worktree metadata, tracked symlink blobs, and paths outside the approved root are rejected/skipped. The Git CLI and database are in-process local trust dependencies. A malicious same-user process racing path replacement or modifying the database/configuration remains outside this local-alpha threat boundary; Windows directory handles do not provide a fully race-free cross-platform walk in this implementation.
- Filename/content secret exclusion is best effort. Review whether source code that contains non-obvious secrets should be registered at all; no automated secret scanner is claimed.
- FTS is lexical, the Python AST is partial, and citations establish source location rather than semantic entailment. Do not treat this as runtime verification or a guarantee that code behaves as the answer states.
- No code editing, shell tools, agent execution, file mutation, dependency installation, deployment, persistent chat, cloud inference, or automatic memory write is included.

## Focused tests

`tests/test_developer_intelligence.py` covers interactive-approval gate behavior, permission checks, root containment and symlinks, Git config include rejection, secret and link exclusion, Python AST metadata, unsupported language behavior, FTS search, incremental blob reuse, stale commits, revocation before/during generation, cross-workspace denial, citation validation, provider completion, migration rollback, and backup/restore. Service integration uses the paired local session, selected workspace, CSRF header, synthetic Git repository, and deterministic fake provider.

Run the focused and full offline checks in PowerShell:

```powershell
py -3.14 -m unittest tests.test_developer_intelligence tests.test_m03_p2a_chat tests.test_m03_p2a_identity -v
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q jarvis_core prototypes eval tests
node --check prototypes/m03_b1/app.js
node --check prototypes/m03_b1/chat-ui-state.mjs
node --test tests/chat_ui_state.test.mjs tests/m03_a_galaxy.test.mjs
git diff --check
```

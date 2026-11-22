# P2-A4 — Connected Knowledge Galaxy interface

## User flow

The existing dark, interactive Knowledge Galaxy remains synthetic and is labeled as such. The left workspace selector loads only `GET /api/v1/workspaces` results from the authenticated session. Selecting a workspace uses `PUT /api/v1/session/workspace`; choosing general chat clears it with CSRF-protected `DELETE /api/v1/session/workspace`. Chat responses and memory mutations carry the expected workspace as a stale-tab consistency check; the service still trusts only session identity and current database grants. If another tab changes workspace during generation, the service drops the answer and the UI refreshes the selection. The galaxy search continues to filter only its sample nodes; the nearby hint directs workspace questions to the command dock.

With no workspace selected, the dock preserves M03-B2 single-turn general chat. With an authorized workspace selected, chat routes through the same `/api/v1/chat` endpoint and displays the validated answer outcome and source cards. Active documents use `[D#]`; approved memories use `[M#]` with active-source, historical-source, or user-authored provenance. Invalid citation responses are labeled evidence, not citations, and the model's factual response is discarded. All excerpts are text-rendered, bounded, and workspace-relative. No absolute source path is returned.

Conversation, latest citation handles, and chat evidence remain in page/session memory. Changing workspace or disconnecting clears the visible transcript and source state. The provider is still selected only by the service at startup. No browser request calls Ollama or LM Studio, no model is downloaded, and there is no automatic cloud route.

## Memory proposal and review

The **Propose** control opens an editor. An evidence-backed proposal can cite only active document citations from the latest valid answer; the alternative is an explicitly user-authored note with no source links. Creating either option only creates a pending candidate. A separate pending list permits text edits while retaining the exact existing source set; core services recapture and validate active source snapshots.

The review dialog displays the exact candidate text, provenance, source paths, and snapshot hashes. **Approve this version** and **Reject** are separate actions. The browser submits the snapshot hashes and explicit confirmation; the server derives the actor and workspace from the paired session, checks current READ and WRITE, and creates a short-lived trusted `DecisionContext`. The core service validates the candidate revision, content/evidence hashes, active source snapshot and replay receipt in its transaction. A model response cannot call candidate endpoints or approve a candidate.

## Accessibility and localization

The document language and direction update between English/LTR and Arabic/RTL. Native select, form, dialog, checkbox, and button controls provide keyboard operation. Dialogs have labelled headings; chat, workspace and memory states use live regions. User text uses automatic direction, filesystem-relative paths remain LTR-isolated, and dynamic strings are set with `textContent`. The node list remains the keyboard-accessible selection path for the canvas. Reduced-motion rendering and the existing 2D galaxy fallback remain in place.

## Windows development startup

Prerequisites: Python with the project dependencies, Node.js for the existing M03-A tests, an existing Jarvis database with an already-created principal and READ permission in at least one workspace, and one explicitly selected installed local model provider. For Bionic, start LM Studio with cloud routing disabled and its native server bound to `127.0.0.1:1235`; Jarvis will not download or discover a model. For Ollama, use an installed local model identifier and ensure server-side cloud routing is disabled. The service itself binds only to `127.0.0.1`.

From `C:\AI Projects\Jarvis`, use a trusted PowerShell window:

```powershell
py -3.14 -m prototypes.m03_b1.service `
  --port 8765 `
  --provider lmstudio `
  --model prism-ml/bonsai-27b `
  --base-url http://127.0.0.1:1235 `
  --timeout 120 `
  --actor-id "<existing-principal-id>" `
  --database "$env:LOCALAPPDATA\Jarvis\jarvis.sqlite3"
```

Replace the actor and database placeholders with an existing, permission-configured identity/database pair. Workspace mode fails at startup if the path is not an existing file or the configured actor has no READ access to an existing workspace. The pairing code is printed only to this terminal. Open `http://127.0.0.1:8765/` in the local browser, enter the one-time code, select an authorized workspace, and ask a question whose answer is present in an active document or an approved memory. Check the answer's citation label and provenance card. Then use **Propose**, edit the pending text, inspect its source snapshot in **Review**, and separately approve or reject it. Select **General chat · no workspace** and ask “How are you?” to verify the general path. Change language to Arabic and confirm RTL layout, keyboard navigation, accessible dialogs, and source path direction.

To smoke-test permission revocation, remove the selected principal's READ grant using the existing trusted database administration workflow and submit a new question. The active workspace must be cleared and the response denied; the request must not silently fall back to general chat. Remove WRITE but keep READ to confirm chat works while candidate creation/approval fails closed.

Stop the service with `Ctrl+C`. This runbook is a procedure for the owner; no real Windows, live Bionic, or real-model acceptance is claimed by the offline test results.

## Offline verification

From the repository root in PowerShell:

```powershell
py -3.14 -m compileall -q jarvis_core prototypes tests
py -3.14 -m unittest tests.test_m03_p2a_chat tests.test_rag tests.test_memory tests.test_memory_search tests.test_m03_p2a_identity -v
py -3.14 -m unittest discover -s tests -v
Get-Content -Raw prototypes/m03_b1/app.js | node --input-type=module --check
node --test tests/m03_a_galaxy.test.mjs
git diff --check
```

Tests use synthetic SQLite data and a fake provider. They do not contact a live model, store a conversation, or touch real user files.

## Limits

This is still a single-user local development alpha on plain HTTP loopback. Pairing is a terminal one-time code and sessions are process-local; this is not production account authentication or protection from malicious same-user software, browser extensions, or a compromised browser. There is no transcript persistence, multi-workspace aggregation, account creation, memory auto-write, model tool use, filesystem mutation, voice, remote listener, or cloud inference. Operator configuration must ensure the local runtime itself has cloud routing disabled; Jarvis accepts only explicitly configured loopback provider endpoints but cannot control hidden provider server settings. Live Windows/provider testing and independent review remain outstanding.

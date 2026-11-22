# P2-A1 — Trusted Principal and Workspace Session Binding

**Status:** P2-A1 established the server/API foundation. P2-A2 now adds evidence-backed chat; A3 and A4 are tracked in their separate checkpoint documents.

## Behavior

- The service accepts an optional trusted `actor_id` configuration. Workspace mode requires an explicit existing SQLite database (or injected `Database`) and a configured actor with READ access to at least one existing workspace. Startup initializes the schema and fails if that binding cannot be validated. The actor is bound to each newly paired process-local session; the identity is never taken from browser JSON or returned to the browser.
- `GET /api/v1/workspaces` requires a valid session and returns only `Database.list_workspaces(session.actor_id)`, which filters to READ grants. It returns only workspace ID, display name, and kind.
- `PUT /api/v1/session/workspace` requires exact Host and Origin, a valid session, CSRF, and bounded JSON. It calls `Database.get_workspace(session.actor_id, workspace_id)` before binding the workspace to that session. Missing and unauthorized IDs share `404 workspace_unavailable`.
- `GET /api/v1/session` includes `active_workspace_id` or `null`, never actor identity. It rechecks the selected workspace and clears it if READ permission is revoked or the database cannot validate it. Listing also clears a selection absent from the current READ-filtered list.
- Pairing, session expiry, cookie flags, chat rate/concurrency limits, general single-turn chat, provider settings, Host/Origin checks, no-CORS behavior, and error redaction remain in place. General chat still does not use the selected workspace.
- Browser payloads containing `actor_id`, `principal_id`, or `user_id` identity fields are rejected on pairing, chat, and workspace selection. Workspace IDs are selectors, never capabilities.

When no actor is configured, legacy M03-B2 general chat continues to work; workspace endpoints fail closed with `503 workspace_service_unavailable`. No actor is silently chosen. A configured principal with no READ grants receives an empty workspace list and cannot select a workspace.

## Configuration

Windows PowerShell, from the repository root, with a principal that already has permission rows in the chosen database:

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

Replace the principal placeholder with the trusted local principal ID already represented in the selected database's workspace permission rows. The service does not create accounts, grant permissions, pick an owner, or seed workspaces. `--database` is required when `--actor-id` is provided; the service verifies the path already exists and that the actor has READ access. Supplying a database without an actor is rejected. Without `--actor-id`, workspace routes are disabled while M03-B2 general chat remains available.

## Windows API smoke check

Run this against a fresh service process so the one-use pairing code has not been redeemed in another browser. Keep the PowerShell terminal private; do not paste the pairing code into logs or chat. The session cookie is retained in a PowerShell web session, including its HttpOnly cookie, and the CSRF token stays in the variable.

```powershell
$base = "http://127.0.0.1:8765"
$pairingCode = Read-Host "Pairing code shown by the Jarvis service"
$web = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$pair = Invoke-RestMethod -Uri "$base/api/v1/session/pair" -Method Post `
  -WebSession $web -Headers @{ Origin = $base } -ContentType "application/json" `
  -Body (@{ pairing_code = $pairingCode } | ConvertTo-Json -Compress)

$workspaceResponse = Invoke-RestMethod -Uri "$base/api/v1/workspaces" -Method Get -WebSession $web
$workspaceResponse.workspaces | Format-Table id, name, kind
$workspaceId = Read-Host "Workspace ID from the authorized list"
$selected = Invoke-RestMethod -Uri "$base/api/v1/session/workspace" -Method Put `
  -WebSession $web -Headers @{ Origin = $base; "X-Jarvis-CSRF" = $pair.csrf_token } `
  -ContentType "application/json" -Body (@{ workspace_id = $workspaceId } | ConvertTo-Json -Compress)
$selected
Invoke-RestMethod -Uri "$base/api/v1/session" -Method Get -WebSession $web
```

Expected: the workspace list contains only READ-authorized workspaces; selection returns the chosen ID; session status returns it as active. Then revoke READ permission through the existing trusted database administration workflow and query session status again: the active ID must become `null`, and selecting the revoked workspace must return `workspace_unavailable`. Do not use a client-supplied actor field. P2-A4 connects the selector to these APIs; the galaxy nodes remain synthetic and are never populated from workspace data.

## Tests and limits

Focused API integration tests are in `tests/test_m03_p2a_identity.py`. They use a temporary SQLite database, synthetic principals/workspaces, and a deterministic fake generator. They cover session binding, identity spoofing, READ-only listing/selection, cross-workspace denial, CSRF/Origin rejection, permission revocation, no-actor legacy chat behavior, and inconsistent configuration.

For P2-A1-only regression verification on Windows from the repository root:

```powershell
py -3.14 -m unittest tests.test_m03_p2a_identity tests.test_m03_b1_local_chat tests.test_storage -v
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q jarvis_core prototypes tests
Get-Content -Raw prototypes/m03_b1/app.js | node --input-type=module --check
node --test tests/m03_a_galaxy.test.mjs
git diff --check
```

No schema migration or production dependency was added. P2-A1 provides no chat retrieval, no memory reads/writes, no candidate routes, no workspace-aware model prompt, and no identity/account provisioning. Future workspace-specific operations must read the actor and active workspace from the session and recheck permissions in the relevant core service immediately before acting; the P2-A1 selection is not itself lasting authorization.

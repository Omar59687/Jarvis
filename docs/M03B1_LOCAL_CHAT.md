# M03-B1 — Secure Local AI Chat Alpha

**Status:** draft implementation for independent review. This slice makes the bottom command dock usable with the existing local Ollama adapter. It is a development alpha, not a production authenticated dashboard.

## Scope

- Preserve the merged M03-A dark Knowledge Galaxy and its synthetic nodes, graph interactions, responsive layout, and English/Arabic RTL support.
- Serve the M03-B1 page and local M03-A visual assets from a separate Python service on IPv4 loopback.
- Pair one browser session using a short-lived one-time code printed only to the trusted PowerShell terminal.
- Accept a single message and return one response from the explicitly selected, already-installed `gemma3:4b` through `jarvis_core.ollama.OllamaClient`.
- Keep all session state and displayed chat in process/browser memory. The next request does not include previous turns; no conversation is persisted.

The chat prompt describes general conversation only. It explicitly says the model has no access to memories, workspaces, files, browser, tools, or the wider computer. The service has no retrieval, SQLite, memory creation, workspace authorization, cloud provider, Tauri bridge, filesystem action, voice, or tool execution route. Do not interpret this alpha as a working knowledge assistant.

## Windows launch and pairing

First ensure Ollama is installed and the model already exists. These commands only inspect; they do not download a model:

```powershell
py -3.12 --version
ollama list | Select-String 'gemma3:4b'
Get-Content (Join-Path $env:USERPROFILE '.ollama\server.json')
```

Ollama cloud routing must be disabled in the Ollama server configuration. The supported server setting is `"disable_ollama_cloud": true` in `%USERPROFILE%\.ollama\server.json`; alternatively set `OLLAMA_NO_CLOUD=1` for the Ollama server process. Restart Ollama after changing its setting and verify its startup log reports cloud disabled. M03-B1 cannot inspect or enforce the Ollama server's cloud configuration. See the [official Ollama cloud FAQ](https://docs.ollama.com/faq#how-can-i-disable-ollama-cloud).

From the repository root, with any previous prototype server stopped:

```powershell
py -3.12 -m prototypes.m03_b1.service --port 8765
```

Keep this PowerShell window trusted and open. It prints `http://127.0.0.1:8765/` and a random pairing credential. Open that numeric loopback URL in the browser, paste the terminal credential into the pairing form, then enter **How are you?** and submit. Stop the service with `Ctrl+C`. If the pairing code expires or has been used, restart the service; a new code is printed. There is no `ollama pull` step and the service never downloads or silently substitutes a model.

The M03-A mock prototype remains separately available through `py -3.12 -m prototypes.m03_a.service --port 8765`; only one service can use a given port at a time.

## Local service and security boundary

The separate Python module `prototypes.m03_b1.service` exposes only the versioned endpoints below, harmless `/health`, and an explicit static asset allowlist. It does not call M02 RAG or any database API.

| Route | Behavior |
|---|---|
| `GET /health` | Harmless health response; indicates no data connection |
| `GET /api/v1/session` | Requires the process-local session cookie; returns its CSRF token and fixed model name |
| `POST /api/v1/session/pair` | Exact Host/Origin and JSON required; redeems the one-time terminal code |
| `POST /api/v1/chat` | Exact Host/Origin, valid session, CSRF header, bounded JSON, then one model generation |
| `DELETE /api/v1/session` | Exact Host/Origin, valid session and CSRF; revokes that session |

Controls implemented in this alpha:

- Bind only to `127.0.0.1`; reject non-exact Host and Origin on state-changing routes; answer no CORS requests and send no `Access-Control-Allow-Origin` header.
- Generate a 256-bit URL-safe pairing secret, print it only to the terminal, expire it after 5 minutes, allow at most five attempts, and consume it once. Credentials are not put in URLs, files, browser bundles, or logs; HTTP access logging is disabled.
- Issue an opaque random `HttpOnly; SameSite=Strict` cookie scoped to `/api/v1`; keep only a hash of the session identifier in a bounded process-local session table. Sessions expire after 30 minutes idle or 4 hours absolute. A synchronizer token is held in browser memory and required in `X-Jarvis-CSRF` for chat and logout.
- Require JSON and bound request body (4 KiB chat, 2 KiB pairing), message length (1,000 characters), model output (16,000 characters), concurrent handlers (16), and chat frequency (6/minute/session, one in flight per session). The Ollama adapter has a finite 120-second per-request timeout and bounded response bytes; the browser allows 150 seconds before aborting. Generation uses a 192-token output limit. These longer waits accommodate the owner's previously observed cold-start range; they do not improve model inference speed.
- Accept a generated answer only when the adapter returns a literal `done=True` completion flag and bounded valid text. Missing, malformed, or false completion status returns only the generic `generation_failed` error.
- Return generic error codes only. Do not write exception details or request values to logs or responses. Fixed model selection is `gemma3:4b` against `http://127.0.0.1:11434`; the existing adapter checks model installation and does not pull models.

The alpha uses ordinary HTTP because it serves a numeric loopback origin, so the session cookie does not have the `Secure` attribute. This is not protection against malicious software running as the same Windows user, browser extensions, local port races, or a compromised browser. Pair only from a trusted local browser and keep the pairing code private. A real production dashboard needs a separately reviewed authentication and transport design. Session state disappears when the process exits.

## Dependencies and platform notes

No new production dependency is introduced. The service and session implementation use Python's standard library and reuse the existing Ollama adapter. The frontend reuses the bundled local M03-A Three.js 0.186.1 assets and license notice; no CDN or external network asset is loaded. The model weights and Ollama runtime are separate from repository dependencies and retain their own licenses and resource requirements.

This code has not been verified on Windows as part of this implementation turn. Confirm the commands above with the target Python launcher, Ollama installation, browser, firewall policy, and WebView2 only if later embedding it. No Tauri build is included.

## Verification

Offline tests use a deterministic fake generator and do not contact Ollama:

```powershell
py -3.12 -m unittest tests.test_m03_b1_local_chat -v
py -3.12 -m unittest tests.test_m03_a_prototype -v
node --test tests/m03_a_galaxy.test.mjs
py -3.12 -m unittest discover -s tests -v
```

The focused tests cover pairing and replay/expiry, bounded retries, missing and expired sessions, Host/Origin/CSRF rejection before generation, request limits, duplicate/rate-limited requests, fixed local model/prompt, error redaction, and frontend use of only the authenticated same-origin endpoint without persistence or direct Ollama calls.

The owner must still run this on Windows with cloud routing disabled, pair the browser, and confirm a real local Gemma response. No successful real Gemma conversation is claimed here.

## Limitations and next gate

- No Windows run or real model response is confirmed by this code change.
- No production dashboard authentication, Windows credential storage, HTTPS, workspace authorization, retrieval, evidence citations, memory writes, audit history, database persistence, streaming, conversation history, multi-user isolation, or account management.
- Ollama cloud disablement is an operator prerequisite and cannot be enforced or detected by the client adapter.
- A browser session is intended only for a single local development user. Any broader deployment requires a new threat review and authorization design.
- Independent code review and owner live testing must precede any claim of an operational local chat experience.

# M03-B2 — Local Model Provider Abstraction and Bionic

**Status:** implementation on `feature/m03-b2-local-provider-abstraction`, based on M03-B1 commit `6cb0635`. This is a restricted local single-turn chat alpha, not a production assistant or authenticated knowledge dashboard.

## Scope and behavior

The M03-B1 loopback service now uses a small provider-neutral `ModelProvider` contract. The browser still calls only the authenticated same-origin Jarvis endpoint. At startup, the operator must explicitly choose a provider, model, and numeric loopback base URL; Jarvis does not set a default model, enumerate models, download or load one, or fall back to another provider.

Two adapters are available:

- `lmstudio`: LM Studio Bionic native `POST /api/v1/chat`. Jarvis sends the selected model, one user input, the existing general-conversation system prompt, `stream: false`, `reasoning: "off"`, `store: false`, a 192-token output cap, and temperature 0.3. It returns only validated `message` output text; reasoning output is discarded and tool-call output is rejected.
- `ollama`: the existing `OllamaClient`, wrapped to implement the shared interface. Its explicit installed-model check and local/cloud metadata protections remain in force. It does not download models.

Both use the same service session, endpoint, chat UI, and normalized result/error contract. Provider changes require a service restart with different explicit command-line values; the browser does not call either model runtime.

## Windows startup and browser smoke test

Use a trusted PowerShell terminal. First start/configure LM Studio Bionic so its native API is listening on `127.0.0.1:1235` and the intended model is available there. Jarvis does not probe the server, request its model list, or load a model. Then, from the repository root:

The Bionic `--base-url` can be the service root (`http://127.0.0.1:1235`) or the exact native endpoint (`http://127.0.0.1:1235/api/v1/chat`); Jarvis normalizes either to the fixed Bionic route.

```powershell
py -3.14 --version
py -3.14 -m prototypes.m03_b1.service --port 8765 --provider lmstudio --model prism-ml/bonsai-27b --base-url http://127.0.0.1:1235 --timeout 120
```

Keep the terminal open. It prints a five-minute, one-use pairing credential. In the same browser profile, open `http://127.0.0.1:8765/`, paste the credential into the pairing form, and submit **How are you?** The response should be ordinary visible message text; no reasoning content is displayed. Stop the server with `Ctrl+C`. This Windows/browser procedure has not been run in this implementation environment; a successful live Bionic response is not claimed.

The alternative explicit Ollama invocation is:

```powershell
py -3.14 -m prototypes.m03_b1.service --port 8765 --provider ollama --model gemma3:4b --base-url http://127.0.0.1:11434 --timeout 120
```

The named model must already be installed. To prevent remote inference, configure the local runtime itself for local-only operation and disable its cloud routing before use. Jarvis rejects non-loopback destinations, proxy routing, redirects, Ollama remote model metadata, and cloud model tags; it cannot control all settings inside separately installed runtime software.

## Service and security boundary

The browser continues to use only `GET /api/v1/session`, `POST /api/v1/session/pair`, authenticated/CSRF-protected `POST /api/v1/chat`, and `DELETE /api/v1/session`. Pairing, exact Host and Origin checks, rate limits, bounded bodies and outputs, one-in-flight-chat policy, ephemeral sessions, and generic error redaction are unchanged. No SQLite, memory, workspace, filesystem, tool, voice, cloud API, or external action is exposed in this milestone.

Provider transport rules:

- Only plain HTTP with an explicit numeric IPv4/IPv6 loopback address and port is accepted. Hostnames, remote IPs, credentials, paths/query overrides, and HTTPS URLs are rejected.
- Requests use a stdlib opener with proxy handling explicitly disabled. Redirects are not followed.
- The Bionic adapter sends no integration/tool fields, disables provider response storage, bounds its response body to 2 MB, caps prompt/system/output sizes, and applies a finite timeout of 0.1–300 seconds (120 seconds by default).
- The model identifier is supplied by the operator and passed to the provider. No discovery, selection, install, download, or fallback request is made.
- Bionic HTTP 400/404 errors map to a generic model-unavailable code; timeout, connection, HTTP, and malformed-response failures return generic browser errors with no provider body, exception detail, or local path.
- Normal Bionic output must be a nonempty bounded `message` string. Reasoning output is never displayed. Tool-call or unknown output types fail closed. Ollama responses still require `done=True`.

This is still plain HTTP on loopback, intended for one local development user. It does not protect against malicious same-user processes, hostile browser extensions, port races, a compromised browser, or insecure settings in a separately installed model runtime. The pairing credential is only printed to the trusted terminal; keep that terminal private. The session is process-local, and response text exists only in browser memory.

## Dependencies, licensing, and limits

No new production package was added; adapters use Python's standard library. LM Studio is an optional separately installed local runtime and is not bundled or imported as a Python/JavaScript dependency. Bionic and model licensing/terms must be reviewed for the actual installed runtime and exact model release before redistribution or other use. Existing bundled M03-A Three.js assets remain local; no CDN is used.

Limitations include no live Bionic check in this environment, no streaming, chat history, retrieval, citations, memory writes, workspace operations, model discovery, runtime lifecycle management, cloud routing, authentication beyond this development pairing workflow, or Windows packaging. The endpoint convention and response parser are exercised with deterministic fake transports in CI; only the owner can confirm a live Bionic install and Windows browser behavior.

## Offline verification

From the repository root:

```powershell
py -3.14 -m unittest tests.test_lmstudio tests.test_m03_b1_local_chat tests.test_ollama -v
py -3.14 -m unittest discover -s tests -v
py -3.14 -m compileall -q jarvis_core prototypes tests
node --check prototypes/m03_b1/app.js
node --test tests/m03_a_galaxy.test.mjs
```

The tests use fake providers/transports; they do not require Ollama, LM Studio, network access, a model download, or a real model response.

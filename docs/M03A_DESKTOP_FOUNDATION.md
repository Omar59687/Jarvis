# M03-A — Desktop Foundation

**Status:** M03-A scope and acceptance record. The synthetic browser/service visual prototype is merged. It is not a production desktop app, authenticated dashboard, or stack approval. M03-B1 separately proposes a restricted local chat alpha; see [M03-B1](M03B1_LOCAL_CHAT.md).

## Scope and result

M03-A compares Tauri 2 + a shared TypeScript UI + the existing Python service direction with a browser-only Python application. It adds a small standard-library Python service at [`prototypes/m03_a`](../prototypes/m03_a/README.md): a bilingual, synthetic knowledge-galaxy demonstration; a harmless health response; loopback binding; a static-file allowlist; Host validation; and security response headers. The graph contains fictional spaces, projects, folders, files, memories, notes, and relationships. Selecting a node focuses the 3D view, highlights its immediate connections, and opens a contextual details panel. A keyboard-accessible node list and simpler 2D fallback are provided. Search filters only sample nodes. The disabled command dock explicitly says chat is unavailable and sends no question. No database, real memory, retrieval, model, filesystem, or write endpoint is present.

No Tauri project, TypeScript package, Python packaging integration, production auth/session service, or installer is added. No production dependency or database behavior changes.

## Architecture comparison and recommendation

| Concern | Tauri 2 + TypeScript UI + Python service | Browser-only Python application |
|---|---|---|
| Windows packaging | Native desktop shell and installer path; must package and supervise a Python child/service, Rust code, frontend assets, and WebView2 assumptions. | Fewer runtime pieces; package Python and open the default browser. No native window or installer experience without an additional wrapper. |
| Startup and shutdown | Desktop shell can own one service process, readiness check, single-instance lock, and graceful shutdown. Parent/child failure handling and packaged Python startup add work. | Straightforward for development; production launch, browser opening, process lifetime, port collision, and shutdown need a tray/launcher or user-managed process. |
| Development complexity | Highest of the options: TypeScript/Node frontend tooling, Rust/Tauri, Python service, Windows C++ toolchain, and cross-process integration. | Lowest initial complexity if kept to Python and standard-library or a single web framework. The existing M01/M02 code is Python. |
| Security | Strong desktop capability scoping is possible, but webview commands, IPC, service credentials, and browser origins must each have explicit boundaries. Tauri itself does not authorize Jarvis workspaces. | Fewer native bridges, but the browser is an untrusted client. Loopback is not authentication; exact origin, CSRF, session pairing, Host validation, and per-request service authorization remain necessary. |
| Memory and disk | Tauri uses the system WebView2 runtime rather than bundling a full browser by default; the Python service and WebView still consume memory. Exact installer and runtime footprint are unmeasured. | Reuses the user's already-running browser and the same Python service. Runtime cost depends on browser reuse and Python packaging; no measurements exist. |
| Long-term reuse | Shared web UI can run inside Tauri and an ordinary browser against the same versioned API. Native capabilities remain behind a narrow desktop bridge. | API can be shared later, but adding a native desktop product may require a shell and additional frontend/service lifecycle work. |

**Recommendation for review:** keep **Tauri 2 + TypeScript UI + Python service** as the leading *conditional* product direction because the confirmed product target includes both a Windows desktop app and browser dashboard with shared UI. Use the browser-only prototype as the cheapest way to exercise UI/service contracts first. Do not lock or build the production stack until the owner verifies the Windows toolchain, packaged Python lifecycle, installer behavior, origin/session design, and a small memory/launch measurement on the target laptop. If minimizing initial complexity becomes more important than a native desktop shell, browser-only Python is the simpler alternative. No memory-use or performance winner is claimed.

## Prototype compatibility spike

Run from the repository root:

```powershell
py -3.12 prototypes/m03_a/service.py --port 8765
```

Open `http://127.0.0.1:8765/`. The harmless `GET /health` endpoint responds with `data_connected: false`; the UI itself has no API calls. Search filters the in-page synthetic nodes. The AI chat field and button are disabled and labeled unavailable, so no question can be submitted. Select nodes from the graph or the keyboard-accessible sidebar list; use the language selector, reduce-motion control, and reset control. If WebGL is unavailable, the prototype displays its local 2D node view. Stop the foreground prototype with `Ctrl+C`.

The spike demonstrates that a Python process can serve the static interface and a harmless health check to a browser using only the standard library. The page makes no API request; the health endpoint is available for manual verification. It does **not** test Tauri, packaged Python, the Jarvis service API, session bootstrap, production CSRF, Windows startup, or a production security boundary. The server is intentionally a development prototype, not a deployment recommendation.

## Proposed local application-service boundary

The service remains the only policy enforcement point. The UI must not connect to SQLite, `Database`, `MemoryService`, `SearchIndex`, or Ollama directly.

1. **Identity:** establish a local user session through a reviewed host-mediated pairing step. Resolve `actor_id` from the authenticated session on the server. Never accept a client-supplied actor as authority.
2. **Workspace authorization:** treat a requested workspace as an untrusted selector. Check the session actor's current permission for that workspace and operation on every request before retrieval, generation, memory writes, or filesystem work. Return only workspace-authorized evidence and stable citation provenance.
3. **Browser session:** use a short-lived, process-bound session established with a one-time pairing secret delivered through a trusted local launch path. Keep secrets out of URLs, logs, frontend bundles, and command-line arguments. Rotate the pairing secret on service restart; expire idle sessions and support explicit logout.
4. **Origin and CSRF:** serve browser UI and API from one exact loopback origin. Validate the `Host` against the bound loopback address and actual port; validate exact `Origin` on unsafe methods; reject `null`, missing, or unlisted origins. Use `SameSite=Strict`, `HttpOnly` session cookies plus a per-session synchronizer token in a custom request header for state-changing requests. Check Fetch Metadata as defense in depth. Do not rely on CORS as CSRF protection.
5. **CORS:** send no CORS headers for the browser API; do not enable wildcard origins, credentials across origins, or arbitrary preflight. The desktop webview should call a narrow host bridge or authenticated service channel rather than widening browser CORS. Tauri command capabilities must be window-specific and minimal.
6. **Listener and lifecycle:** bind only to numeric loopback, prefer an ephemeral port, and never expose a remote listener. The desktop parent starts one service child, waits for a bounded readiness result, handles startup failure without opening an unauthenticated API, and performs authenticated graceful shutdown before a bounded process-termination fallback. Use a single-instance guard and clean up the child on parent exit. A public `/health` may reveal only liveness/version class, never user, workspace, DB, or model state.
7. **Errors and audit:** return stable error codes and a request ID; map invalid input, unauthenticated, unauthorized, conflict, rate-limit, and internal failures to bounded responses. Do not return stack traces, SQL details, paths, tokens, prompts, evidence text, or personal content in errors/logs. Audit security-sensitive lifecycle and approval events without logging secrets or unnecessary content.

These are design requirements for a shared production application service. The M03-A mock prototype implements only loopback binding, exact Host checking, a static path allowlist, restrictive response headers, no CORS, harmless health, and rejecting all write methods. It has no auth session; therefore it exposes no private data endpoint. M03-B1 has a separate development-alpha session design with no workspace or personal-data endpoint.

## Shared interface and language requirements

The first shared interface direction places a workspace selector and local sample search on the left; the interactive knowledge galaxy in the center; a contextual selection/details panel and language control on the right; and a Jarvis command dock at the bottom. This revision intentionally uses a visual prototype badge and fictional nodes. Search is a local filter over the eight in-memory sample nodes. AI chat is explicitly unavailable: controls are disabled and no question is accepted. There are no answer, evidence-retrieval, model, or settings integrations, and it does not imply that `gemma3:4b` was queried or any Jarvis source was retrieved. The first secure functional chat and workspace-search integration is a later slice requiring authenticated local sessions and server-side authorization.

English is left-to-right and Arabic switches the document to right-to-left, including the side navigation and context panel placement. Controls have visible keyboard focus; every graph node can also be selected through the keyboard-operable sample node list. The graph reduces motion in response to the operating-system preference and has an explicit reduce-motion toggle. On unsupported WebGL, it falls back to a simpler 2D node layout. Before production UI work, validate mixed Arabic/English text, filenames, numbers, selection focus, and screen-reader behavior in Windows WebView2 and supported browsers. M03-B1 connects only the bottom chat dock to the local single-turn service; its boundaries are in [M03-B1](M03B1_LOCAL_CHAT.md).

Future connected screens must preserve logical reading order, keep citations adjacent to claims in both directions, mirror directional controls carefully, and test mixed Arabic/English text, numbers, filenames, and citation labels on Windows WebView2 and supported browsers.

## Dependencies, licenses, and platform constraints

- **Prototype service:** Python standard library only.
- **Prototype visualization:** Three.js **0.186.1**, release tag `r186`, vendored as the local ES module pair `prototypes/m03_a/vendor/three.module.js` and `three.core.js`; upstream commit `9b4a2ac29c63ccb43fd51c5661f2f873ac2c39b8`. SHA-256 hashes: `three.module.js` `9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea`; `three.core.js` `9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6`. Three.js is MIT licensed; the matching upstream notice is included at `prototypes/m03_a/vendor/THREE-LICENSE.txt`. These two modules are one pinned prototype dependency, with no add-ons, CDN, remote fonts, external textures, npm install, or runtime package graph. Its prototype-only status should be revisited before production adoption.
- **Proposed UI:** TypeScript (Apache-2.0) and Node.js (MIT) are not added here. Pin exact versions and review transitive npm dependencies, scripts, and lockfile before adoption.
- **Proposed desktop:** Tauri 2 project licensing is MIT/Apache-2.0; Rust toolchain and Microsoft C++ Build Tools are required for Windows development. Verify exact plugin and bundler licenses at adoption time.
- **WebView2:** Microsoft runtime/deployment terms apply separately from the app. The Evergreen runtime is shared and updated by Microsoft; check availability during install/startup and provide a clear missing-runtime path. A Fixed Version runtime adds substantial bundled size and maintenance responsibility; no selection is made in this slice.
- **Python distribution:** PyInstaller or another packager is not selected or installed. Compare packaging maintenance, antivirus false positives, signing, update behavior, and included Python/native-library licenses before choosing one.
- No model, model asset, voice component, Tauri runtime/plugin, or frontend package manager dependency is installed. The only bundled third-party code is the pinned Three.js core module described above.

### Windows prerequisite checks

The current official Tauri 2 guide lists Microsoft C++ Build Tools with **Desktop development with C++**, Microsoft Edge WebView2, and Rust with the MSVC host toolchain for Windows development. A TypeScript frontend also needs Node.js and a package manager. WebView2 is present on many supported Windows versions, but installations must still check availability and handle clean Windows/LTSC/server cases. MSI builds may require the Windows VBSCRIPT optional feature; that is not needed for this prototype.

Run these checks in **PowerShell on the target Windows laptop**; no result is inferred from this Linux workspace:

```powershell
$PSVersionTable.PSVersion
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsBuildNumber, OsArchitecture
py -3.12 --version
node --version
npm --version
rustc --version
cargo --version
rustup show active-toolchain
rustup target list --installed
where.exe cl
```

Confirm Visual Studio Build Tools includes the native desktop C++ workload and x64 MSVC tools (run from a Visual Studio Developer PowerShell when checking `cl.exe`):

```powershell
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path $vswhere) {
  & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
```

Check WebView2 registration and installation directories rather than assuming Windows installed it:

```powershell
reg query 'HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients' /s /f 'WebView2'
reg query 'HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients' /s /f 'WebView2'
$programFilesX86 = ${env:ProgramFiles(x86)}
Get-ChildItem (Join-Path $programFilesX86 'Microsoft\EdgeWebView\Application') -ErrorAction SilentlyContinue
Get-ChildItem (Join-Path $env:ProgramFiles 'Microsoft\EdgeWebView\Application') -ErrorAction SilentlyContinue
```

Do not install prerequisites as part of this slice. If the owner later approves a real Tauri spike, record exact Windows version, Rust target, C++ workload, WebView2 version, Node/package-manager versions, and build output before assessing compatibility.

## Automated checks and acceptance criteria

Offline checks verify loopback binding, harmless health payload, required security headers, no CORS, strict Host validation, rejection of mutating/private API paths, static asset allowlisting, a disabled/no-submit chat preview, bilingual layout, local Three.js asset usage, and deterministic graph selection/search behavior. Run:

```powershell
py -3.12 -m unittest tests.test_m03_a_prototype -v
node --test tests/m03_a_galaxy.test.mjs
py -3.12 -m unittest discover -s tests -v
py -3.12 prototypes/m03_a/service.py --port 8765
```

M03-A is ready for stack review when:

- This comparison, recommendation, boundary design, risk inventory, and acceptance plan are reviewed.
- The prototype serves the bilingual synthetic galaxy and health-only endpoint over IPv4 loopback.
- Selecting graph nodes updates the details panel and neighborhood highlighting; reduced motion and the 2D fallback remain usable.
- The chat dock is visibly unavailable and cannot submit questions.
- Automated tests demonstrate there is no private-data API, wildcard CORS, mutation route, or path traversal through the prototype.
- Existing M01/M02 tests still pass.
- The owner runs the Windows prerequisite checks and reports results before any real Tauri build.
- The owner explicitly approves the stack before the real dashboard, service authentication/session implementation, or packaging work begins.

The UI-health spike and automated tests pass only for the prototype. Production session security, authentication, workspace policy integration, packaged lifecycle, Windows compatibility, installer behavior, memory/launch performance, and Tauri capability configuration remain unverified.

## Security test plan for the next implementation slice

- Spoofed actor ID and cross-workspace requests remain denied even when UI hides other workspaces.
- Missing, expired, revoked, and replayed local sessions; invalid/missing CSRF token; unexpected, `null`, or missing Origin; hostile Host/DNS-rebinding inputs; cross-site form and fetch attempts.
- No CORS credential reflection or wildcard headers; arbitrary preflight stays denied.
- Request size/rate bounds; stable redacted errors; server process crash/startup timeout/port collision/parent shutdown; one-instance behavior.
- Tauri command capability denies unknown windows, commands, paths, shell access, and remote origins by default.
- Test against the exact Windows WebView2 runtime and supported browser versions; review local malicious-process limits separately because loopback and same-user controls are not a boundary against a hostile process running as the same user.

## Known limitations

- The prototype is browser-only and synthetic-only; no Tauri or Windows build ran in this Linux environment.
- Three.js is vendored only for this visual prototype; graph layout, labels, WebGL compatibility/performance, Arabic typography, and screen-reader experience need review on the target Windows WebView2/browser.
- No service authentication or session flow is implemented. Only the non-sensitive health endpoint is exposed.
- The user-supplied target laptop prerequisites are unknown until checked there; no tool availability is inferred.
- The comparison has no measured startup, memory, installer-size, or performance results.
- Tauri, TypeScript, Node, Python bundling, and WebView2 deployment choices remain subject to owner approval and exact-version/license/dependency review.
- This plan does not change M01/M02 authorization, storage, retrieval, model, or lifecycle code.

## Primary references checked on 2026-11-05

- [Tauri 2 Windows prerequisites](https://v2.tauri.app/start/prerequisites/) — C++ Build Tools, WebView2, Rust/MSVC toolchain.
- [Tauri capabilities](https://v2.tauri.app/security/capabilities/) and [runtime authority](https://v2.tauri.app/security/runtime-authority/) — command and window capability model.
- [Tauri Windows installer](https://v2.tauri.app/distribute/windows-installer/) — installer targets and packaging requirements.
- [Microsoft WebView2 distribution](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution) and [Evergreen versus Fixed Version](https://learn.microsoft.com/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version) — availability and deployment trade-offs.
- [TypeScript repository/license](https://github.com/microsoft/TypeScript) and [Node.js repository/license](https://github.com/nodejs/node) — candidate frontend toolchain.

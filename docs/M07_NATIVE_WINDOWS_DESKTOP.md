# M07 — Native Windows Desktop Assistant

**Status:** Implemented a dependency-free Windows tray companion on this feature branch. It opens the existing loopback dashboard in the user's default browser, reports backend liveness, and never launches or terminates the Jarvis backend.

## Architecture decision

The existing functional UI is the authenticated M03-B2 dashboard in `prototypes/m03_b1`; its Python `LocalChatServer` binds to numeric IPv4 loopback and provides `/health`, the pairing/session flow, workspace authorization, and the existing voice APIs. Voice remains in the dashboard's push-to-talk UI and uses its authenticated `/api/v1/voice/*` endpoints. There is no separately packaged production dashboard or authenticated backend lifecycle endpoint in this baseline.

| Approach | Fit for this milestone | Cost and decision |
|---|---|---|
| Tauri 2 + web UI + Python service | Reuses a shared web UI and offers native shell plugins; would add Rust, Node build tooling, WebView2 assumptions, plugin review, and packaged Python lifecycle. | Existing M03-A comparison keeps it a conditional future product direction. Not selected for this small companion; no Tauri/Node/Rust dependency is introduced. |
| PySide6 | Python-native widgets and system-tray APIs simplify native UI, but Qt-for-Python adds a large runtime/dependency and a licensing/packaging review. | Not selected: a second embedded webview/widget layer is unnecessary when the existing dashboard already works in the authenticated browser. No PySide6 dependency is added. |
| Python standard library + Win32 APIs | `ctypes` calls a narrow set of Windows shell, named-mutex, and registered-hotkey APIs. The existing UI remains in the browser. | **Selected:** no package installation, no embedded credentials, and no broad WebView bridge. Native behavior is isolated to `prototypes/m07_desktop/desktop.py`; non-Windows invocation exits gracefully. |

Official references consulted **2026-11-23**: Microsoft [`RegisterHotKey`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey), [`Shell_NotifyIconW`](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw), and [`CreateMutexW`](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw); Tauri [Windows prerequisites](https://v2.tauri.app/start/prerequisites/) and [global shortcut plugin](https://v2.tauri.app/plugin/global-shortcut/); Qt for Python [`QSystemTrayIcon`](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html) and [licensing](https://doc.qt.io/qtforpython-6/licenses.html). Recheck upstream requirements and licenses before changing the selected stack.

## Implemented behavior

- **Tray and dashboard:** a Windows notification-area icon opens `http://127.0.0.1:<port>/` in the default browser. The separate voice action (`Open voice controls`) opens `http://127.0.0.1:<port>/#voice-panel`; the companion does not handle microphone access or audio. On current `main`, `prototypes/m03_b1/index.html` defines `<section id="voice-panel">` inline beneath the Knowledge Galaxy stage with no JavaScript `location.hash` / `hashchange` routing in `prototypes/m03_b1/app.js` (relying on native browser fragment scrolling, with voice controls gated on browser pairing and configured local STT/TTS). Older pre-M03-C1 `m03-b2` dashboard instances do not serve `#voice-panel` even though `/health` returns `{"status":"ok","service":"m03-b2"}`.
- **Authentication:** no cookie, pairing secret, CSRF token, principal ID, or workspace data is read by the companion or put in a URL. The browser uses its normal same-origin session. Pairing remains the existing trusted-terminal workflow. A different browser profile may require pairing again.
- **Backend status:** a bounded, redirect-refusing GET to the fixed numeric-loopback `/health` endpoint checks for the expected M03-B2 liveness response. It sends no credentials and reads at most 2,049 bytes. Health status is liveness only, not proof of backend identity or session authorization.
- **Lifecycle:** launching the tray app is a user action; there is no startup registration or auto-start. Exit removes only this process's tray icon, hotkey registration, timer, window, and named mutex. It never starts, restarts, signals, or terminates the backend or any unrelated process. The existing backend remains managed using its documented operator command because it has no authenticated lifecycle API.
- **Single instance and shortcut:** a named mutex limits the companion to one instance per interactive Windows session. The second launch opens the dashboard and exits. The default global shortcut is `CTRL+ALT+J`; configure with `--shortcut CTRL+SHIFT+J`. A conflict or registration failure leaves the tray usable and reports the shortcut as unavailable. Only `RegisterHotKey` is used; there is no keyboard hook or key stream monitoring.
- **Notifications:** backend connected/disconnected transitions are the only events. Notifications default off; pass `--notifications` or explicitly toggle them in the tray menu. Fixed generic text contains no workspace, transcript, model output, or personal content.

## Windows installation and packaging

No installer or executable is built by this milestone. For source use, install a supported Python 3 interpreter using the project's normal Windows setup, open PowerShell at the repository root, and run:

```powershell
py -3 -m prototypes.m07_desktop --port 8765
```

For a custom shortcut or notification opt-in, use e.g. `py -3 -m prototypes.m07_desktop --port 8765 --shortcut CTRL+SHIFT+J --notifications`. The companion does not start a server. Start the existing M03-B2 service separately with its documented provider/model settings, then pair through the trusted terminal flow if needed. There is no optional startup registration; place a shortcut in Windows Startup only if the user explicitly chooses to do so outside the app.

Packaging is **Open**. A future signed installer must package the Python application and static code without bundling model weights, credentials, user databases, or audio; declare the supported Windows/Python versions; include uninstall and upgrade behavior; review executable signing and SmartScreen; and test tray recovery after Explorer restarts. No packager or third-party dependency is adopted here.

## Limits and validation

- **Implemented, testable:** fixed loopback URL construction, bounded no-redirect health checks, strict shortcut parsing, opt-in generic status notifications, shortcut conflict fallback, duplicate-instance refusal, and graceful cleanup through a fake host.
- **Windows-only, not integration-tested here:** actual tray rendering, Windows notification-area behavior, hotkey conflicts with other applications, shell notification suppression/settings, mutex behavior across sessions, and Explorer restart recovery. The source test suite does not launch the companion or restart Jarvis/Bonsai.
- A local process can imitate `/health`, occupy the named mutex, or race for the dashboard port. The companion's liveness check is not authenticated identity proof. Do not enter pairing credentials in a page unless its local service and origin are recognized.
- Windows notifications may be suppressed or transformed by OS policy and Focus Assist. Generic content limits reduce exposure but do not control OS notification history.
- This tray helper is not a backend supervisor, service installer, automation agent, or microphone monitor. It creates no new authorization and does not widen M04/M06 APIs or permissions.

## Windows manual acceptance and automated verification

### Verified manual Windows results (2026-11-23, Zen browser, `http://127.0.0.1:8765`)

| Check | Result | Notes |
|---|---|---|
| System tray icon | **PASS** | Tray icon rendered in Windows notification area |
| Backend connectivity | **PASS** | Companion detected running loopback `m03-b2` `/health` endpoint |
| Dashboard opens in Zen | **PASS** | Default browser (Zen) opened the loopback dashboard |
| Tray → Open Dashboard | **PASS** | Menu action opened `http://127.0.0.1:8765/` |
| Global shortcut `CTRL+ALT+J` | **PASS** | Shortcut opened the dashboard |
| Tray → Voice Control URL launch | **OBSERVED** | Opened `http://127.0.0.1:8765/#voice-panel` |
| Voice interface rendering | **NOT VERIFIED** | Opened page displayed Knowledge Galaxy; see `#voice-panel` findings below |
| Remaining Windows edge cases | **NOT TESTED** | Single-instance launch, clean exit/shutdown cleanup, shortcut conflict handling, opt-in notifications/transitions, and Explorer restart recovery remain untested on Windows |

### `#voice-panel` investigation

- **Current branch (`feature/m07-native-windows-desktop-assistant` / `main`):** `prototypes/m03_b1/index.html` (46,424 bytes in Git) defines `<section id="voice-panel" class="voice-panel">` inline inside `<main id="galaxy">` directly below `<div class="galaxy-stage">` (Knowledge Galaxy), with the push-to-talk `#voice-ptt` button in `#command-dock`. `prototypes/m03_b1/app.js` contains no `location.hash` or `hashchange` handler, so `#voice-panel` is not a dedicated view and relies solely on native browser fragment scrolling; voice controls also remain disabled until the browser is paired and local STT/TTS backends are configured.
- **Live backend tested on `http://127.0.0.1:8765`:** Read-only inspection returned `200 OK` on `/health` (`{"status":"ok","service":"m03-b2","data_connected":false}`), a 22,539-byte pre-M03-C1 dashboard HTML containing `Knowledge galaxy` but **no** `id="voice-panel"` and **no** `voice-ptt`, and `404 Not Found` on `/voice-ui-state.mjs`. Because `/health` reports liveness only (`service: "m03-b2"`), the companion cannot detect whether the running backend includes the M03-C1 voice panel. Voice rendering and voice operation remain **NOT VERIFIED**.

### Automated test and CI results (2026-11-23)

- Focused M07 tests (`python -m unittest tests/test_m07_desktop.py -v`): **13 passed** (Windows, Python 3.14.10).
- Full Python regression suite (`python -m unittest discover -s tests`): **402 run, OK (392 passed, 10 skipped)** (Windows, Python 3.14.10).
- Python syntax compilation (`python -m compileall -q jarvis_core prototypes tests eval`): **PASS**.
- Node regression suite (`node --test tests/*.test.mjs`): **24 passed** (Node v26.13.1).
- Dashboard JavaScript syntax (`node --check prototypes/m03_b1/app.js`): **PASS**.
- GitHub Actions CI on implementation commit `432ed0532725c64be28f7d115161c781903db6e6`: `ubuntu-latest Python 3.12` **PASS**, `windows-latest Python 3.12` **PASS**.

### Release-blocking vs. optional items

- **Release-blocking before merge:**
  - Manually verify remaining Windows lifecycle and edge cases: single-instance launch behavior (second launch opens dashboard and exits cleanly), clean exit (`Exit Jarvis companion`) removing tray icon/hotkey without touching the backend, global shortcut conflict fallback, and opt-in notification toggle/transition balloons.
  - Verify `Tray → Open voice controls` (`/#voice-panel`) against a current `main` / M03-C1+ backend build (when the operator is ready to run it), or explicitly scope/revise the voice menu action with approval.
- **Optional post-M07 improvements (non-blocking):**
  - Add explicit `#voice-panel` hash-scroll/focus/highlight handling in `prototypes/m03_b1/app.js`.
  - Handle Windows `TaskbarCreated` messages for tray recovery after Explorer restarts.
  - Deduplicate double-click tray activation (`WM_LBUTTONUP` + `WM_LBUTTONDBLCLK`), remove unused Win32 constants (`MF_CHECKED`, `ERROR_HOTKEY_ALREADY_REGISTERED`), add a branded Jarvis tray icon, and optionally expose backend capability metadata or packaging/signing later.

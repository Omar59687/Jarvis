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

- **Tray and dashboard:** a Windows notification-area icon opens `http://127.0.0.1:<port>/` in the default browser. The separate voice action opens the existing dashboard at `#voice-panel`; the companion does not handle microphone access or audio.
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

Focused synthetic coverage is in `tests/test_m07_desktop.py`. Full Python/Node regression evidence is recorded after running the suite for the implementation commit; a target-user Windows UI smoke check remains a manual acceptance step.

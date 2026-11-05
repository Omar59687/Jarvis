# M03-A compatibility prototype

This is a stdlib-only, mock browser interface and health check. It does not connect to Jarvis data or APIs.

From the repository root:

```powershell
py -3.12 prototypes/m03_a/service.py --port 8765
```

Open `http://127.0.0.1:8765/`; stop with `Ctrl+C`. The only API-like route is `GET /health`, which reports `data_connected: false`. Workspace, search, chat, citations, and settings are sample UI content.

The prototype is a compatibility spike, not a production server. See [M03-A scope and acceptance](../../docs/M03A_DESKTOP_FOUNDATION.md) for the trust boundary, limitations, Windows checks, and test plan.

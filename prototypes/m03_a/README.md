# M03-A knowledge-galaxy visual prototype

A browser-only visual demonstration backed by a harmless Python health check. All graph items, labels, descriptions, and relationships are fictional synthetic content. This is not a working Jarvis assistant and cannot answer questions.

From the repository root:

```powershell
py -3.12 prototypes/m03_a/service.py --port 8765
```

Open `http://127.0.0.1:8765/`; stop the service with `Ctrl+C`. Select a node in the 3D graph or the left node list to focus its neighborhood, highlight direct connections, and show its sample details. Drag to orbit; use the wheel to zoom. The search field only filters the eight bundled synthetic nodes. Toggle Arabic to inspect RTL layout. The motion control and operating-system reduced-motion setting disable idle animation. If WebGL cannot initialize, a simpler selectable 2D view appears.

The bottom chat field and button remain disabled with an explicit unavailable status. No question can be submitted. The page does not make API calls; it loads local static assets. `GET /health` remains available as a harmless manual health check. There is no Jarvis data, Ollama, SQLite, memory/search API, authenticated session, or private endpoint.

Three.js 0.186.1 (release tag `r186`) is bundled locally as a core ES module pair. Its MIT notice is in `vendor/THREE-LICENSE.txt`; version, upstream commit, and integrity hash are recorded in [the M03-A scope document](../../docs/M03A_DESKTOP_FOUNDATION.md). No CDN or remote assets are used.

For visual verification, follow the steps above and check: node selection/details/connections, connection focus changes, keyboard selection from the sidebar list, search clearing/filtering, English/Arabic direction, reduced motion, reset, and disabled chat. To exercise the fallback, use a browser with WebGL disabled and reload. Windows-specific WebView2 verification remains an owner-side step.

See [M03-A scope and acceptance](../../docs/M03A_DESKTOP_FOUNDATION.md) for security boundary, stack comparison, Windows prerequisite checks, tests, and remaining limits.

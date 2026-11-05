# Jarvis 2.0 — Open-Source Stack Audit

**Audit date:** 2026-11-05 (M03-A Windows desktop and shared UI screen; original component inventory from 2026-11-03)
**Repository inspected:** initial inventory covered `Omar59687/Jarvis` at `ac32b9c927ac71f897cbd49b4d463c95c3b5014b`; later milestone adoption states are updated below.  
**Scope:** public upstream documentation and repository metadata; no downloaded code was executed and no dependency was installed.

## 1. Recommendation

Adopt a staged, evidence-first stack:

- **M01 candidate:** SQLite with migrations; test whether the selected runtime build includes FTS5. Keep persistence behind a storage interface.
- **Desktop/UI candidate:** evaluate Tauri 2 plus a shared TypeScript web UI, with narrow capabilities; compare packaging and security cost against a simpler browser-only local UI before locking it.
- **Local model:** M02-A implements a standard-library adapter for a replaceable local Ollama endpoint. No model is selected or bundled. Ollama software licensing does not license every model it can run.
- **3D visualization candidate:** Three.js for the knowledge galaxy, deferred until the dashboard milestone.
- **Voice candidates:** faster-whisper for local speech-to-text and openWakeWord for wake-word detection, both subject to Windows/hardware and model-asset checks.
- **Integration candidates:** official MCP Python SDK, Playwright, OpenCode, and REA only in later milestones with version pinning, central approval policy, and isolation.
- **Agent instruction content:** Matt Pocock's skills and REA's agent skill files are instructions, not executable integrations. Review and adapt individual workflows; do not install or treat instructions as a security boundary.

Nothing in this list is installed or adopted in code. “Candidate” means suitable for a bounded compatibility/security spike, not approved production dependency.

## 2. Comparison

| Capability | Candidate and license | Reuse type | Fit and trade-offs | Security/licensing notes | Adoption |
|---|---|---|---|---|---|
| Local storage and text search | [SQLite](https://www.sqlite.org/copyright.html) is public domain; [FTS5](https://www.sqlite.org/fts5.html) is included in SQLite amalgamation | Runtime/library | Small local footprint; good M01 fit; FTS availability depends on shipped SQLite build | Check SQLite build options; use parameterized queries, migrations, backups, and secure-delete/retention design; wrappers have their own licenses | **Proposed for M01** |
| Local model runtime | [Ollama](https://github.com/ollama/ollama), MIT software license | Runtime/service | M02-A adapter uses its local tags and chat APIs; model choice and memory fit remain open | Model weights have separate terms; review exact model card/license and source. Adapter restricts configured endpoint to numeric loopback, rejects redirects, and does not authenticate other local processes | **Adapter implemented; runtime separately installed; no model selected or bundled** |
| Windows desktop shell | [Tauri 2](https://github.com/tauri-apps/tauri), MIT/Apache-2.0 project licensing | Application framework | Reuses web UI and offers scoped native commands; Rust, C++ build tools, WebView2 and packaging add complexity | Scope capabilities by window/command; audit plugins and origin model; WebView2/build prerequisites documented upstream | **Leading conditional direction in M03-A review; no Tauri app/runtime installed or built** |
| Shared web UI toolchain | [TypeScript](https://github.com/microsoft/TypeScript), Apache-2.0; [Node.js](https://github.com/nodejs/node), MIT | Proposed build-time language/toolchain | Could share typed screens across Tauri and an ordinary browser; introduces Node/npm packages, build scripts, lockfile and supply-chain maintenance | Pin runtime/tool versions; inspect npm scripts and full dependency closure; no frontend package is adopted by the prototype | **Proposed for review; not installed or added** |
| Knowledge galaxy | [Three.js](https://github.com/mrdoob/three.js), MIT | UI library | General-purpose WebGL 3D library; suitable rendering base, but graph layout, accessibility, and large-graph performance remain Jarvis work | Preserve MIT notice and audit addon/font/asset licenses separately | **Candidate for M03** |
| Speech recognition | [faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT | Python library/runtime | CTranslate2-based Whisper transcription; local operation; CPU may be practical, GPU setup needs measurement | Models and downloaded assets have separate terms; check exact CTranslate2/CUDA/Windows compatibility and memory. No inference benchmark performed | **Candidate for M03** |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord), Apache-2.0 code | Python library/model assets | Purpose-built phrase detection; separate audio pipeline and platform dependencies | Verify each included model/voice asset's terms and provenance; inspect native wheels and microphone consent/privacy | **Candidate for M03** |
| MCP support | [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk), MIT | Client/server SDK | Direct fit for Python tool integration; supports multiple transports | SDK support policy and security advisories change; pin a supported release, monitor advisories, validate tool schemas, isolate transport and require central authorization. MCP protocol support is not a permission system | **Candidate for M04** |
| Browser automation | [Playwright](https://github.com/microsoft/playwright), Apache-2.0 | Automation library | Mature cross-browser automation; useful for later authorized, user-directed workflows | Browser sessions can contain account data and perform consequential actions; dedicated profile, domain policy, approval, and safe download handling required | **Candidate for M07** |
| Coding-agent integration | [OpenCode](https://github.com/anomalyco/opencode), MIT | External agent application / API | Reuse an existing coding agent rather than rebuild coding loops; integrate through a narrow process/API boundary | Permission configuration is powerful and defaults can be permissive; run with explicit deny/ask policies in isolated worktrees. Model/provider terms are separate | **Candidate for M04/M06** |
| Authorized reverse engineering | [REA](https://github.com/morluto/rea), MIT for REA-authored code | External tool + MCP server + instruction skills | Broad app/runtime/binary-analysis workflows may save substantial effort | Large dependency/tool surface; third-party material retains its licenses; commercial tools are separately licensed. Use only for owned/authorized targets and an isolated copy. Re-audit exact release and notices before adoption | **Evaluate in M05; not installed** |
| Agent workflow instructions | [mattpocock/skills](https://github.com/mattpocock/skills), MIT repository license | Agent instruction files | Reusable methods/prompts can inform coding workflows; instructions do not implement tools or prove agent behavior | Review files as untrusted project content, remove conflicting or overbroad directions, pin a reviewed revision, and do not treat a skill as authorization enforcement | **Read/reference only; selectively adapt later** |

## 3. Key distinctions

### Software versus instructions

- **Executable software** includes application frameworks, libraries, runtimes, SDKs, MCP servers, and browser automation tools. It has dependencies, execution surfaces, and runtime security concerns.
- **Agent skills** are instruction documents that describe a workflow. They may influence agent behavior but cannot enforce workspace permissions, restrict operating-system access, or guarantee correctness.
- **Model weights and datasets** are separate artifacts with their own terms and provenance. An inference runtime's license does not automatically cover them.
- **Commercial analysis tools** integrated by REA retain their own licensing and entitlement requirements.

### Proposed adoption stages

1. **M01:** M01-A through M01-E are merged. Cross-workspace consent remains unresolved and denied by default; see the acceptance matrix.
2. **M02-A:** local-only Ollama adapter implemented with no model download. Model selection and performance evaluation are open; target RAM and Windows version are now recorded in [M02-A adapter documentation](M02A_OLLAMA_ADAPTER.md), but CPU and free disk remain unknown.
3. **M02-M03:** benchmark local model and speech candidates on the actual target Windows laptop before selecting models or claiming resource fit.
3. **M04 onward:** integrate one connector at a time behind explicit policies; review security advisories and test least privilege.
4. **M05-M07:** adopt REA, OpenCode, or Playwright only for user-authorized targets/tasks with isolation and an explicit approval path.

## 4. Supply-chain and security controls

Before adding any dependency or external tool:

- Pin a release or commit; record its upstream URL, exact version, package/license, transitive dependency inventory, and update owner.
- Read the license at the exact revision and identify bundled assets, models, fonts, native code, and third-party notices.
- Review release provenance, security policy/advisories, maintenance activity, and open vulnerabilities.
- Prefer official releases and integrity-checked package sources; do not run install scripts or downloaded binaries without review.
- Keep runtime/connector permissions minimal. Do not expose local services publicly by default.
- Require explicit approval for writes, external communications, file changes outside approved roots, or high-impact actions.
- Re-run license and security checks when versions or lockfiles change.

## 5. Evidence and limitations

This audit consulted official project repositories and documentation linked above, plus upstream SQLite documentation. It is a point-in-time screen, not legal advice, a complete transitive-license audit, a penetration test, or a compatibility test. No candidate was installed, built, benchmarked, or executed. License labels must be reconfirmed against the exact revision and all shipped model/assets before redistribution or deployment.

## 6. Primary references

- [SQLite copyright and FTS5](https://www.sqlite.org/copyright.html), [FTS5 docs](https://www.sqlite.org/fts5.html)
- [Ollama repository and security policy](https://github.com/ollama/ollama)
- [Tauri capability model](https://v2.tauri.app/reference/acl/capability/) and [Windows prerequisites](https://v2.tauri.app/start/prerequisites/)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [openWakeWord](https://github.com/dscripka/openWakeWord)
- [MCP Python SDK security policy and advisories](https://github.com/modelcontextprotocol/python-sdk/security)
- [Playwright](https://github.com/microsoft/playwright)
- [OpenCode permission documentation](https://opencode.ai/docs/permissions/)
- [REA license and third-party notices](https://github.com/morluto/rea)
- [Matt Pocock skills](https://github.com/mattpocock/skills)

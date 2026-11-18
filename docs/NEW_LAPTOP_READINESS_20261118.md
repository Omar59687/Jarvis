# Jarvis — New Laptop Readiness & M03-B1 Test Plan

**Date:** 2026-11-18  
**Status:** Proposed instructions only. **No local tests executed on new laptop by this documentation task.**  
**Target:** Windows laptop; user reports RTX 5060 8 GB VRAM and 16 GB system RAM. Confirm actual hardware/driver/runtime measurements on the machine.

## Non-destructive inventory (PowerShell)

Execute locally in a normal (not administrator) PowerShell terminal, inside the intended Jarvis checkout. Output may contain local folder and account names; redact before sharing. Do not print or upload `.env`, tokens, API keys or proprietary sources.

```powershell
git status --short --branch
git rev-parse HEAD
git remote -v
py --version
python --version
node --version
npm --version
nvidia-smi
Get-CimInstance Win32_OperatingSystem | Select-Object Caption, OSArchitecture, @{Name="AvailableRAM_GB";Expression={[math]::Round($_.FreePhysicalMemory/1MB,2)}}
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{Name="FreeGB";Expression={[math]::Round($_.Free/1GB,1)}}
Get-Command ollama, lms -ErrorAction SilentlyContinue | Select-Object Name,Source
```

**Caution:** The remote names in `git remote -v` may embed private hostnames or credentials in badly configured remotes; redact before sharing. Do not run `git pull`, install models or start untrusted scripts as part of this inventory.

## Reproducible environment decision

- Keep existing Ollama adapter as baseline; do not replace it merely because LM Studio is present.
- Discover only locally installed models and running endpoints, ideally using local native UI/CLI after version confirmation.
- Avoid running both runtimes with large resident models simultaneously on 16 GB RAM without measurement.
- Record driver, GPU VRAM, free system RAM, model exact revision/license/quantization, model context, temperature/load, startup state, and GPU-offload configuration.
- Create an **approved synthetic** English/Arabic test suite with citations, abstention, prompt-injection, tool-denial and fixed-length coding questions. No project secrets in external prompts.
- Benchmark cold and warm starts with at least 3 repetitions and report median/variance, first-token latency, end-to-end latency, errors, RAM and VRAM utilization. Treat context size as a memory cost.

## M03-B1 closure gate

PR #14: https://github.com/Omar59687/Jarvis/pull/14 (open draft at time of check). Verify the exact PR head before testing.

1. Fetch and checkout a **separate** review branch/worktree; preserve current checkout and any uncommitted changes.
2. Read `docs/M03B1_LOCAL_CHAT.md`, prototype README, service code, tests and `AGENTS.md` from the PR head.
3. Execute targeted Python security/unit tests, M03-A regression, JavaScript graph tests and standard Python suite, recording exact commands, counts and skips.
4. Start approved local model service, confirm loopback-only pairing, then run one actual browser chat from the prototype. Record request, response, latency, result, and any timeout. Do not relax authentication/CORS restrictions to make it work.
5. Test service unavailability, model timeout, session invalidation, network isolation, refusal to leak another workspace and UI accessibility.
6. Independently review source diff, security implications and runtime logs before presenting owner with merge-ready/not-ready decision.
7. **No merge without explicit owner approval.**

## Workstation migration caveats

GitHub stores pushed commits, **not** ignored `.env` files, local SQLite databases, Ollama/LM Studio model weights, Docker volumes, virtual environments, caches or unsaved edits. Restore required private configuration through an approved secure channel; keep user data and tokens out of GitHub.

## Stop conditions

Stop and report rather than retrying indefinitely if: unknown repository state; missing credentials; storage corruption; unexpected network egress; permission escalation; new security failure; unreproduced test claims; failing backup restore; or unsuitable model memory consumption.

# Jarvis 2.0 — Project Specification

**Status:** foundation requirements are implemented through M01-D; M01-E acceptance review is in progress. Requirements below describe intended product behavior and do not imply that later milestones are implemented.  
**Date:** 2026-11-03

## 1. Purpose

Jarvis 2.0 is a personal AI second brain for remembering and finding useful information, working across explicitly separated personal and project contexts, and eventually connecting to development and productivity tools. It is inspired by Zubair Trabzada's Jarvis system, while this repository's requirements and architecture remain independently defined here.

The product should work locally by default where practical. Cloud model routing is optional, transparent, user-configured, and must never be required for core storage or retrieval.

## 2. Users and intended outcomes

The primary user wants to:

- Save, organize, and retrieve durable personal or project knowledge with source evidence.
- Search approved local folders without granting access to the rest of the computer.
- Keep personal and project information separate unless cross-workspace access is explicitly approved.
- Use Arabic and English.
- Use a Windows desktop client and a browser dashboard.
- Later connect local/cloud models, voice, MCP/API tools, GitHub, OpenCode, reverse-engineering workflows, and coding agents.
- Review suggestions and approve actions before Jarvis writes permanent memories or changes external state.

## 3. Confirmed requirements

These statements are user-confirmed product requirements, not claims of implementation.

### Product and experience

- Windows desktop application and browser dashboard.
- Dynamic, interactive 3D knowledge galaxy.
- Arabic and English.
- Personal and project workspaces.

### Memory and data

- Persistent, evidence-backed memory.
- Automatic synchronization from approved local folders only.
- Explicit approval before saving permanent memories.
- Cross-workspace retrieval requires permission.
- Memory deletion must be reversible.

### Intelligence and integrations

- Local Ollama models, with optional cloud model routing.
- Voice assistant, wake word, and local speech recognition.
- MCP and API integration support.
- GitHub and OpenCode integration.
- Reverse engineering through REA, for software the user owns or is authorized to examine.
- Multi-agent software development workflows.
- Proactive suggestions without unsolicited execution.
- Future bounded autonomous Mission Mode.

### Cost and hardware constraints

- No mandatory paid APIs or subscriptions.
- Target hardware: Intel i7-1165G7 and NVIDIA MX450 with 2 GB VRAM.
- System RAM is not confirmed. Model size, quantization, context length, speech workload, and GPU offload therefore remain open validation items.

## 4. Scope and guardrails

### In scope

- Define requirements, architecture, component adoption criteria, and milestone acceptance criteria.
- Build a local-first foundation in M01 after approval.
- Later add model, desktop, voice, integration, reverse-engineering, and agent capabilities through gated milestones.

### Not implemented by the M01 foundation

- Model installation, package installation, or runtime setup for later milestones.
- Accessing personal files outside the repository.
- Running reverse engineering, browser automation, coding agents, or Mission Mode.
- Treating a package, model, skill, or MCP server as trusted merely because it is open source.

### Product safety and trust rules

- A suggestion is not an action. Require an approval event before permanent memory writes or external side effects.
- Enforce workspace access in the trusted service/storage layer, not only in prompts or the UI.
- Treat retrieved documents, code, web pages, MCP responses, and model output as untrusted input.
- Show which evidence supports a memory or answer and which workspace it came from.
- Make sync roots explicit and revocable. Never infer broad home-directory access.
- Keep deletion recoverable until the user explicitly purges it; communicate retention and purge behavior.
- Keep autonomous execution disabled until a separate milestone defines scoped tools, budgets, stop conditions, audit logs, and recovery.

## 5. Proposed quality attributes

These are architecture goals to validate, not measured properties.

- **Local-first:** memory CRUD and search work without cloud credentials or network access.
- **Privacy:** local content stays local unless a user-approved feature sends it elsewhere.
- **Security:** least privilege, loopback-only local service, explicit tool permissions, secret redaction, and auditability.
- **Recoverability:** database backup/migration procedure and reversible soft deletion.
- **Performance:** responsive basic search on the expected personal corpus; establish numeric targets after a representative corpus and hardware are measured.
- **Accessibility and language:** keyboard support, readable layouts, and Arabic right-to-left plus English left-to-right flows.
- **Maintainability:** modular boundaries, pinned dependency versions, migration discipline, and documented license inventory.

## 6. Open questions

Resolve these before the related implementation gate:

1. System RAM, available disk, Windows version, and whether GPU acceleration is a requirement or an optimization.
2. First M01 data types and a realistic size/sample corpus for performance tests.
3. Whether the browser dashboard binds only to loopback and how it authenticates local browser sessions.
4. Memory lifecycle: retention defaults, user export format, hard-purge UX, and backup encryption expectations.
5. Whether project workspaces can have multiple users later, or remain single-user locally.
6. Arabic normalization/search behavior and the expected handling of mixed Arabic/English queries.
7. Cloud provider selection, routing controls, cost display, and redaction policy.
8. REA version, supported targets, precise dependency/legal review, and isolation plan before M05.
9. Preferred voice output engine and license constraints for speech models/voices.
10. Whether M01 stores only source references/metadata or also snapshots/extracted text; retention and privacy implications differ.

## 7. Acceptance of this document

This specification captures the provided requirements and identifies proposals as proposals. It does not certify an implementation, component choice, security review, or performance result.

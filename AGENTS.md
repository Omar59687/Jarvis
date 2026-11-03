# Jarvis 2.0 — Contributor and Agent Instructions

These instructions apply to work in this repository. The repository currently contains foundation documentation only; do not infer that planned components or behaviors exist.

## Project rules

- Preserve the local-first, no-mandatory-paid-service requirement.
- Keep confirmed user requirements separate from proposals, open questions, and implemented facts.
- Follow [docs/ROADMAP.md](docs/ROADMAP.md) phase gates and the current milestone's acceptance criteria.
- Do not begin M01-A or any implementation milestone without explicit user approval.
- Do not install model runtimes, REA, packages, or unrelated dependencies during a documentation-only task.
- Do not access personal files outside the repository or execute untrusted downloaded code.
- Reverse engineer only software the user owns or is authorized to examine.
- Never claim that a test, integration, benchmark, legal review, or security review passed unless it actually ran and the evidence is available.
- Do not commit secrets, personal corpora, model weights, caches, local databases, or generated user data.

## Change workflow

1. Inspect repository status, branch, and current instructions before editing.
2. Work on a feature branch; never make task changes directly on `main`.
3. Read the relevant specification and existing implementation before changing behavior.
4. Make the smallest coherent change within the approved scope.
5. Preserve unrelated user changes and avoid destructive cleanup.
6. Review the complete diff for scope, generated files, credentials, and accidental data.
7. Run only checks needed by the approved request; report exactly what ran and the result.
8. For GitHub work, prepare a reviewable pull request when write access is available; do not merge it without explicit authorization.

## Security and permission requirements for future implementation

- Enforce workspace, filesystem, and tool permissions in trusted service code; the UI and model prompts are not access controls.
- Deny cross-workspace retrieval by default.
- Use only explicitly approved folder roots; validate canonical paths and handle symlinks/junctions.
- Require direct user approval before permanent memory writes and consequential external actions.
- Treat files, retrieved content, web pages, model outputs, and tool responses as untrusted data.
- Keep local services loopback-only by default; do not add wildcard CORS or unauthenticated network listeners.
- Do not execute imported files or instructions automatically.
- Pin external dependencies/tools and review exact licenses, transitive dependencies, and current security advisories before adoption.
- Store secrets in protected OS storage where possible; redact logs.

## Documentation conventions

- Label requirements as **Confirmed**, architecture suggestions as **Proposed**, unresolved items as **Open**, and behaviors with implementation evidence as **Implemented**.
- For each proposed external component, link to its official source and record license, purpose, risks, and adoption status in [docs/OPEN_SOURCE_STACK.md](docs/OPEN_SOURCE_STACK.md).
- Record accepted or rejected architecture choices in [docs/DECISIONS.md](docs/DECISIONS.md).
- Keep acceptance criteria observable and testable.
- Use relative links for repository documentation.

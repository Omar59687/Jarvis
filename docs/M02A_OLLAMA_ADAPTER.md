# M02-A — Local Ollama Adapter

**Status:** M02-A implementation slice. It provides a bounded HTTP client; it does not yet provide RAG, memory search, prompt construction from retrieved data, or user-facing chat.

## Adapter interface

`jarvis_core.ollama.OllamaClient` is configured with an explicit local base URL, a non-empty allowlist of exact local model identifiers, and optionally an injected transport. `generate(prompt, model, system_prompt=None, settings=...)` first checks `/api/tags`, then uses `/api/chat` with streaming disabled. The response is returned as a `GenerationResult`. The adapter never calls Ollama's model-pull endpoint and never chooses a substitute model. `GenerationSettings.think` is a boolean sent as the chat request's top-level `think` field and defaults to `false`.

The default transport uses Python's standard library. It bounds response bytes and connection time, rejects redirects, ignores environment proxy settings, and accepts only plain HTTP on an explicit numeric loopback IP and port. This numeric-IP requirement avoids trusting local name resolution. The default generation settings use temperature 0, a fixed seed, 4096 context tokens, at most 384 generated tokens, and `think: false`; callers may set a lower context up to 4096, an output limit up to 512, or enable thinking explicitly. Ollama models may not honor the thinking setting. Prompt, system prompt, and response sizes are bounded in the adapter.

The adapter is not an authentication boundary against another process running as the same OS user. A future local host must bind its own service to loopback, authenticate callers, apply workspace authorization before generation, and treat model output as untrusted. The adapter does not accept tools or grant filesystem/database access.

## Local deployment

- Install and operate Ollama separately. This repository does not install Ollama or download model weights.
- Start Ollama locally and configure its HTTP endpoint explicitly, for example `http://127.0.0.1:11434`.
- Select a model that is already installed and add its exact local tag to the application allowlist. The adapter rejects cloud tag forms ending in `:cloud` or `-cloud` (case-insensitive), as well as Ollama tag metadata that identifies a remote model/host. Missing or remote allowlisted models produce an error; the adapter does not pull or substitute.
- For a server-wide local-only policy, configure Ollama itself with `OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud: true` in the server configuration, then restart the Ollama service. Ollama documents this as disabling cloud features. Apply this to the environment/configuration actually used by the Windows Ollama service; the Python client does not change it.
- The Ollama service must be reachable on the configured loopback IP and port. Remote, HTTPS, URL credentials, extra paths, query strings, fragments, and redirects are rejected.
- The adapter is built against the `/api/tags` and non-streaming `/api/chat` API shapes. Runtime compatibility should be verified against the installed Ollama version during deployment.
- Inference is not inherently deterministic across hardware, runtime versions, or model builds. Temperature and seed make settings repeatable, not outputs guaranteed identical.

### Local-only boundary and alias limitation

Ollama documents cloud names with both a `:cloud` tag and legacy/specific cloud tags such as `qwen3-coder:480b-cloud`. Its current `/api/tags` schema can include `remote_model` and `remote_host` fields; the adapter rejects entries that expose these fields, cloud-looking canonical model names, and cloud suffixes in either format. It also rejects a chat response that reports remote routing, but that is a post-request warning and cannot undo transmission.

An allowlisted local-looking alias cannot be proven local by this Python client if the server omits or misreports its remote metadata. The client cannot inspect or attest that the running Ollama process has cloud disabled. Configure `OLLAMA_NO_CLOUD=1` (or `disable_ollama_cloud: true` in `~/.ollama/server.json`) on the Ollama server and restart it for the actual local-only enforcement boundary. Server configuration is controlled by the host administrator and may be changed independently of this client.

References: [Ollama cloud model naming](https://ollama.com/blog/cloud-models), [Ollama local-only/cloud configuration](https://docs.ollama.com/faq), and the [Ollama API schema](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml) (`remote_model`/`remote_host`).

## Target hardware record and open evaluation

User-confirmed Windows target: Windows 11 Home 64-bit (build 26100), 15.77 GB system RAM, NVIDIA GeForce MX450 with 2 GB VRAM, NVIDIA driver 581.83, Ollama 0.40.14, and CUDA 13.0 as reported by NVIDIA-SMI. CPU model and free disk space remain unknown.

No model is selected. RAM/VRAM capacity alone does not establish whether a model or a chosen context fits in GPU memory; Ollama may use system memory or partial GPU offload. Do not infer latency, throughput, quality, thermals, or memory use without measurements on the target Windows laptop. The adapter's initial 4K context ceiling and conservative output cap are safety bounds, not a model-fit or performance claim.

## M01 constraints carried forward

- M01 has no combined cross-workspace retrieval consent workflow; cross-workspace aggregation remains unsupported and denied by default.
- The trusted-host memory decision signature does not authenticate a person or prove a human clicked. No interactive UI or authentication service exists.
- Document search remains lexical FTS5; Arabic clitic handling, stemming, semantic retrieval, and cross-language matching are not provided.
- SQLite is unencrypted, and historical backups can retain earlier content. Existing Windows filesystem race limitations for hostile concurrent local changes remain.

## Verification

Adapter unit tests use injected fake responses and a loopback-only HTTP server to verify URL policy, model gating, request bounds/settings, malformed responses, and redirect rejection without Ollama, model downloads, or Internet access. Passing these tests does not verify live Ollama compatibility or Windows inference performance.

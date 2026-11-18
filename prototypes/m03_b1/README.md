# M03-B2 local provider chat alpha

This separate service reuses the synthetic M03-A Knowledge Galaxy and connects its bottom dock to a bounded single-turn endpoint. Select either LM Studio Bionic or Ollama explicitly at startup; both adapters use the provider-neutral generation contract. The page is available only from the service started on `127.0.0.1`.

For LM Studio Bionic, ensure its native API is available on loopback and the selected model is ready. From the repository root on Windows, run:

```powershell
py -3.14 -m prototypes.m03_b1.service --port 8765 --provider lmstudio --model prism-ml/bonsai-27b --base-url http://127.0.0.1:1235 --timeout 120
```

Open the numeric loopback URL printed in PowerShell and enter the one-time code shown in that same trusted terminal. The code expires in five minutes and is consumed once. The model is never downloaded or discovered by this service. Stop it with `Ctrl+C`.

For Ollama, pass `--provider ollama --model <installed-local-model> --base-url http://127.0.0.1:11434`; disable runtime cloud routing. This is not production authentication or a connected knowledge assistant. Only general conversation is sent to the configured local model; no workspace, memory, file, browser, tool, or prior-turn content is supplied. No chat is persisted. See [M03-B2 provider and security notes](../../docs/M03B2_LOCAL_PROVIDER.md) for operator steps, test commands, and limitations.

On the P2-A1 feature branch, workspace list/select APIs can be enabled by adding `--actor-id <existing-principal-id>` and optionally `--database <sqlite-path>`. The principal must already have workspace permission rows in that database; the service does not create users or grants. These APIs do not change general chat or connect the synthetic workspace picker. See [P2-A1 Windows verification and limits](../../docs/P2A1_TRUSTED_WORKSPACE_SESSION.md).

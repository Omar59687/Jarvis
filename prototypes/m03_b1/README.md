# M03-B1 local chat alpha

This separate service reuses the synthetic M03-A Knowledge Galaxy and connects its bottom dock to a bounded single-turn endpoint backed by the existing local Ollama adapter. The page is available only from the service started on `127.0.0.1`.

From the repository root on Windows, first verify that Ollama cloud routing is disabled and that `gemma3:4b` is already installed. Then run:

```powershell
py -3.12 -m prototypes.m03_b1.service --port 8765
```

Open the numeric loopback URL printed in PowerShell and enter the one-time code shown in that same trusted terminal. The code expires in five minutes and is consumed once. The model is never downloaded by this service. Stop it with `Ctrl+C`.

This is not production authentication or a connected knowledge assistant. Only general conversation is sent to the local model; no workspace, memory, file, browser, tool, or prior-turn content is supplied. No chat is persisted. See [M03-B1 scope and security notes](../../docs/M03B1_LOCAL_CHAT.md) for operator steps, test commands, and limitations.

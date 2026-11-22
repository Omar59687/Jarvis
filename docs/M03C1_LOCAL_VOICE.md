# Jarvis M03-C1 — Local Voice Foundation (Push-to-Talk)

M03-C1 adds a local, privacy-preserving, push-to-talk voice loop to the M03-B2 browser dashboard while preserving every authentication, CSRF, Host/Origin, workspace permission, rate-limit, and non-execution boundary.

## Scope & Safety Posture

- **Push-to-talk only:** Microphone permission (`navigator.mediaDevices.getUserMedia`) is requested **only** when the user explicitly clicks the **Talk** (`تحدث`) button. No wake-word detection or always-on listening is active (deferred to M03-C2).
- **Strict offline operation:**
  - Speech-to-text (`FasterWhisperSTTAdapter`) requires an explicit local CTranslate2 model directory (`model.bin` + `config.json`), enforces `local_files_only=True`, and sets `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`.
  - Text-to-speech (`ChatterboxMultilingualTTSAdapter` or `PiperTTSAdapter`) requires explicit local model directories/files and never downloads weights or voices at runtime.
  - **No silent fallback:** Jarvis never falls back to browser `window.speechSynthesis`, browser `SpeechRecognition`, or any cloud speech service. If TTS is unconfigured or unavailable, the text reply remains visible and a clear local error is shown.
- **Privacy & zero persistence by default:**
  - Raw browser recordings are encoded in-memory as 16 kHz mono PCM16 WAV (bounded to 30 s and 2 MiB), validated in memory via Python's `wave` module, and passed to `faster-whisper` via `io.BytesIO`. No audio files or speech transcripts are written to disk or SQLite.
  - Spoken reply audio is returned with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`, played via an ephemeral local `Blob` URL (`media-src 'self' blob:`), and revoked on replacement, workspace change, disconnect, session expiry, or page close (`pagehide`/`beforeunload`).
  - Active microphone tracks (`MediaStreamTrack.stop()`) and `AudioContext` are released immediately on stop, error, disconnect, session expiry, workspace switch, and page close.
- **No action execution or approval bypass:** Recognized speech is routed directly into the existing authenticated `POST /api/v1/chat` pipeline with session cookie, CSRF token, Host/Origin checks, workspace permissions, and rate limits intact. Voice cannot execute tools, modify files, approve memory candidates, or bypass orchestration approvals.

## Architecture

| Layer | Module | Responsibility |
| --- | --- | --- |
| Core voice adapters & persona | `jarvis_core/voice.py` | In-memory PCM16 WAV validation, provider-neutral `SpeechToTextProvider` / `TextToSpeechProvider` protocols, `FasterWhisperSTTAdapter`, `ChatterboxMultilingualTTSAdapter`, `PiperTTSAdapter`, `VoiceService`, and configurable Jarvis butler persona (`default` / `jarvis`). |
| Local HTTP service | `prototypes/m03_b1/service.py` | Authenticated endpoints `GET /api/v1/voice/status`, `POST /api/v1/voice/transcribe`, and `POST /api/v1/voice/synthesize`; per-session voice rate limits and concurrency guards; optional `voice_mode` flag in `POST /api/v1/chat`; CLI flags for explicit local backends. |
| Dashboard voice state | `prototypes/m03_b1/voice-ui-state.mjs` | Pure state machine (`stopped`, `listening`, `transcribing`, `thinking`, `speaking`, `error`), EN/AR localization, interrupt/session teardown transitions, and browser-side 16 kHz PCM16 WAV encoder (eliminating `ffmpeg` dependency on Windows). |
| Dashboard UI | `prototypes/m03_b1/index.html`, `app.js`, `chat.css` | Push-to-talk button, state indicator, `Play` / `Stop` / `Mute` / `Transcript` controls, recognized transcript display, interrupt handling, and strict media teardown. |

## Local TTS Backend Evaluation (Windows 11 + RTX 5060)

Measured host hardware during M03-C1 development:
- **GPU:** NVIDIA GeForce RTX 5060 (8 GiB VRAM, Driver 582.05, CUDA 13.0, Blackwell architecture `sm_120`)
- **Base Python:** `3.14.10` (system/base conda)
- **Existing isolated conda env:** `jarvis-ai` (`Python 3.12.16`)

### Option A — Chatterbox Multilingual (`--tts-backend chatterbox`)
- **Strengths:** Natural neural prosody with native bilingual English (`en`) and Arabic (`ar`) support in a single model, plus optional local reference voice WAV (`--tts-voice-ref`) for an original Jarvis voice identity. Fits within the RTX 5060's 8 GiB VRAM (~3–5 GiB active).
- **Windows 11 compatibility caveat:** `chatterbox-tts` depends on PyTorch + `torchaudio` and does **not** currently ship wheels for Python 3.14. On RTX 50-series (Blackwell `sm_120`), it also requires a PyTorch build with CUDA 12.8+ support inside a **Python 3.11 or 3.12** isolated environment.
- **Offline loading:** `ChatterboxMultilingualTTSAdapter` loads strictly via `ChatterboxMultilingualTTS.from_local(model_dir, device=...)` from an explicitly downloaded local checkpoint directory.
- **Pinned bundle check:** readiness requires `ve.safetensors`, `t3_mtl23ls_v2.safetensors`, `s3gen.safetensors`, and `tokenizer.json`, matching the pinned `chatterbox-tts==0.1.7` local multilingual bundle. The runtime forces Hugging Face/Transformers offline environment flags before loading.

### Option B — Piper ONNX (`--tts-backend piper`) — Lightweight Fallback
- **Strengths:** Self-contained native Windows binary (`piper.exe`) + small local `.onnx` / `.onnx.json` voices (~60–100 MB each, e.g. `en_GB-alan-medium` for composed British English and `ar_JO-kareem-medium` for Arabic). Runs fast on CPU with **zero** PyTorch/CUDA/Python-version constraints — works even when running the service on Python 3.14.
- **Offline loading:** `PiperTTSAdapter` executes the explicit local `piper` binary with `shell=False` and `--output-raw`, streaming PCM16 directly from stdout into an in-memory WAV container (no temporary files written to disk).
- Piper output is read in bounded chunks and the process is killed on output-budget overflow or timeout. Chatterbox inference runs under a per-adapter lock and cannot be interrupted inside PyTorch; a Stop action aborts the browser request and suppresses late playback, while the server inference may finish before another call uses that adapter.

### Offline boundary

The environment flags and explicit local paths are application-level offline controls. They prevent supported model loaders from resolving remote model identifiers and prevent this voice code from initiating download requests. They are **not** OS-enforced network isolation and do not guarantee that unrelated libraries or the host process cannot access the network. For a hard offline guarantee, run Jarvis under an OS/network policy that denies outbound connections.

## Isolated Python Environment Setup (Windows 11)

Core Jarvis tests run on standard library Python (including Python 3.14). To run **real local STT/TTS** without polluting system Python:

### 1. Create or reuse a Python 3.12 isolated environment

Using Conda (reusing or cloning a Python 3.12 env such as `jarvis-ai`):

```powershell
conda create -n jarvis-voice python=3.12 -y
conda activate jarvis-voice
pip install -r requirements-voice.txt
```

Or using `venv` with Python 3.12:

```powershell
py -3.12 -m venv .venv-voice
.\.venv-voice\Scripts\Activate.ps1
pip install -r requirements-voice.txt
```

### 2. Place local models on disk ahead of time (no runtime downloads)

1. **STT (`faster-whisper`):** Place a local CTranslate2 Whisper model directory (containing `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`/`vocabulary.json`), for example:
   `C:\AI Models\whisper\faster-whisper-small` (supports both English and Arabic).
2. **TTS — Piper (lightweight CPU option):** Place `piper.exe` and voice files locally, e.g.:
   - `C:\AI Models\piper\piper.exe`
   - `C:\AI Models\piper\en_GB-alan-medium.onnx` + `en_GB-alan-medium.onnx.json`
   - `C:\AI Models\piper\ar_JO-kareem-medium.onnx` + `ar_JO-kareem-medium.onnx.json`
3. **TTS — Chatterbox Multilingual (GPU option, Python 3.12 + CUDA 12.8+ PyTorch):**
   ```powershell
   pip install --pre torch torchaudio --index-url https://download.pytorch.org/whl/cu128
   pip install chatterbox-tts
   ```
   Download the Chatterbox Multilingual checkpoint files manually into a local folder such as `C:\AI Models\chatterbox-multilingual` (and optionally place a short clean original voice sample at `C:\AI Models\chatterbox-multilingual\jarvis_ref.wav`).

### 3. Launch the local service with explicit offline voice configuration

Example with LM Studio (Bonsai) + `faster-whisper` + `piper` + Jarvis butler persona:

```powershell
python -m prototypes.m03_b1.service `
  --provider lmstudio `
  --model "your-installed-local-model" `
  --base-url "http://127.0.0.1:1234" `
  --persona jarvis `
  --stt-backend faster-whisper `
  --stt-model-path "C:\AI Models\whisper\faster-whisper-small" `
  --stt-device cpu `
  --stt-compute-type int8 `
  --tts-backend piper `
  --tts-binary-path "C:\AI Models\piper\piper.exe" `
  --tts-model-path "C:\AI Models\piper\en_GB-alan-medium.onnx" `
  --tts-model-ar-path "C:\AI Models\piper\ar_JO-kareem-medium.onnx"
```

Example with Chatterbox Multilingual on CUDA:

```powershell
python -m prototypes.m03_b1.service `
  --provider lmstudio `
  --model "your-installed-local-model" `
  --base-url "http://127.0.0.1:1234" `
  --persona jarvis `
  --stt-backend faster-whisper `
  --stt-model-path "C:\AI Models\whisper\faster-whisper-small" `
  --stt-device cuda `
  --stt-compute-type float16 `
  --tts-backend chatterbox `
  --tts-model-path "C:\AI Models\chatterbox-multilingual" `
  --tts-device cuda `
  --tts-voice-ref "C:\AI Models\chatterbox-multilingual\jarvis_ref.wav"
```

## Persona Configuration

- `--persona default` (default): preserves the exact M03-B2 general chat system prompt (`GENERAL_CHAT_SYSTEM_PROMPT`).
- `--persona jarvis` (and voice-initiated turns via `voice_mode: true`): enables the composed, concise, polite Jarvis butler persona with subtle humor, occasional "sir", bilingual English/Arabic support, and explicit non-execution / no-memory-claim guardrails.
- In workspace-scoped RAG mode, `DocumentRAG`'s strict anti-injection and citation system prompt remains authoritative and is never weakened by persona styling.

## Current Limitations & Verification Status

- Automated verification in this milestone covers synthetic Python (`unittest`) and Node (`node --test`) suites for WAV validation, offline adapter enforcement, HTTP auth/CSRF/Host/Origin/rate-limit/error-redaction boundaries, zero disk/DB persistence, UI state transitions, interrupt handling, and session teardown.
- **Live end-to-end Windows hardware verification** (physical microphone capture in Edge/Chrome, real `faster-whisper` weights on English & Arabic speech, real Chatterbox Multilingual / Piper synthesis, and browser audio playback) has **not** yet been run in this branch and must be completed before declaring production-ready voice conversation.
- Wake-word detection, continuous duplex conversation, and voice-triggered tool/task execution remain intentionally out of scope for M03-C1.

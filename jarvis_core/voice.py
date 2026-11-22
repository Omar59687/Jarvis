"""Offline local voice foundation for Jarvis (M03-C1).

Security and privacy invariants:
- Provider-neutral speech-to-text (STT) and text-to-speech (TTS) interfaces.
- Strict offline operation: local model paths are required explicitly,
  ``local_files_only=True`` is enforced for faster-whisper, Chatterbox
  Multilingual loads only via ``from_local`` from a verified local directory,
  and Piper runs an explicit local binary with explicit local ONNX voice files.
- Never silently falls back to cloud speech APIs or browser speech synthesis.
- Never stores raw audio recordings or speech transcripts on disk by default:
  validation, transcription, and synthesis operate in memory.
- Configurable Jarvis persona preserves all non-execution and non-fabrication
  safety guardrails in both English and Arabic.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import threading
from typing import Any, Callable, Protocol
import wave


VOICE_MAX_AUDIO_BYTES = 2 * 1024 * 1024  # 2 MiB (~65s @ 16 kHz mono PCM16)
VOICE_MIN_AUDIO_SECONDS = 0.1
VOICE_MAX_AUDIO_SECONDS = 30.0
VOICE_MIN_SAMPLE_RATE = 8000
VOICE_MAX_SAMPLE_RATE = 48000
VOICE_MAX_CHANNELS = 2
VOICE_MAX_TRANSCRIPT_CHARS = 1000
VOICE_MAX_TTS_CHARS = 2000
VOICE_MAX_TTS_AUDIO_BYTES = 8 * 1024 * 1024  # 8 MiB bounded WAV synthesis output
DEFAULT_VOICE_TIMEOUT_SECONDS = 60.0

SUPPORTED_VOICE_LANGUAGES = frozenset({"en", "ar"})
SUPPORTED_STT_LANGUAGE_HINTS = frozenset({"auto", "en", "ar"})
SUPPORTED_STT_DEVICES = frozenset({"auto", "cpu", "cuda"})
SUPPORTED_STT_COMPUTE_TYPES = frozenset({"default", "int8", "float16", "int8_float16", "float32"})
SUPPORTED_TTS_DEVICES = frozenset({"cpu", "cuda"})

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_CITATION_TAG_RE = re.compile(r"\s*\[[DM][1-9][0-9]*\]")
_WHITESPACE_RE = re.compile(r"\s+")


class VoiceError(Exception):
    """Base error for offline local voice processing."""


class VoiceConfigurationError(VoiceError):
    """Raised when a voice backend or persona is misconfigured."""


class VoiceAudioValidationError(VoiceError):
    """Raised when uploaded or synthesized WAV audio fails validation."""


class VoiceModelUnavailableError(VoiceError):
    """Raised when a required local voice model or runtime is unavailable."""


class VoiceTranscriptionError(VoiceError):
    """Raised when local speech-to-text transcription fails."""


class VoiceSynthesisError(VoiceError):
    """Raised when local text-to-speech synthesis fails."""


class VoiceTimeoutError(VoiceError):
    """Raised when a local voice backend times out."""


@dataclass(frozen=True)
class ValidatedWavAudio:
    """Validated in-memory uncompressed PCM16 WAV metadata."""

    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int
    duration_seconds: float
    byte_length: int


@dataclass(frozen=True)
class TranscriptionResult:
    """Normalized in-memory transcription output."""

    text: str
    language: str | None
    duration_seconds: float
    backend: str


@dataclass(frozen=True)
class SynthesisResult:
    """Normalized in-memory WAV synthesis output."""

    audio_bytes: bytes
    content_type: str
    sample_rate: int
    duration_seconds: float
    language: str
    backend: str


@dataclass(frozen=True)
class VoicePersonaProfile:
    """Configurable Jarvis conversation persona."""

    name: str
    display_name: str
    instructions: str


JARVIS_PERSONA_INSTRUCTIONS = (
    "Adopt the Jarvis persona: composed, concise, intelligent, polite, and quietly attentive, "
    "with restrained, subtle British-butler humor and an occasional natural 'sir' when appropriate. "
    "Maintain an original voice identity; never imitate copyrighted characters or actors. "
    "Keep spoken answers clear, natural, and brief unless detail is explicitly requested. "
    "If the user speaks or writes in Arabic, reply fluently and politely in Arabic while preserving "
    "the same composed, helpful tone. "
    "Never claim to have executed tasks, run commands, modified files, sent messages, or accessed "
    "external systems; if asked to perform an action, state plainly that voice chat is conversational "
    "only and cannot execute actions."
)

PERSONA_PROFILES: dict[str, VoicePersonaProfile] = {
    "default": VoicePersonaProfile(
        name="default",
        display_name="Standard Local Assistant",
        instructions="",
    ),
    "jarvis": VoicePersonaProfile(
        name="jarvis",
        display_name="Jarvis Butler Persona",
        instructions=JARVIS_PERSONA_INSTRUCTIONS,
    ),
}


def validate_persona_name(name: str) -> str:
    """Validate and normalize a configured persona name."""
    if not isinstance(name, str) or name.strip().lower() not in PERSONA_PROFILES:
        raise VoiceConfigurationError(
            f"persona must be one of: {', '.join(sorted(PERSONA_PROFILES))}"
        )
    return name.strip().lower()


def resolve_chat_system_prompt(
    base_prompt: str,
    *,
    persona: str = "default",
    voice_mode: bool = False,
) -> str:
    """Build the system prompt for general local chat while preserving safety rules.

    When ``persona == 'default'`` and ``voice_mode`` is False, returns
    ``base_prompt`` verbatim so existing M03-B2 general chat behavior and tests
    remain unchanged.
    """
    normalized = validate_persona_name(persona)
    if normalized == "default" and not voice_mode:
        return base_prompt
    profile_name = "jarvis" if (normalized == "jarvis" or voice_mode) else "default"
    profile = PERSONA_PROFILES[profile_name]
    if not profile.instructions:
        return base_prompt
    return f"{base_prompt} {profile.instructions}"


def detect_voice_language(text: str, hint: str | None = None) -> str:
    """Choose ``'en'`` or ``'ar'`` from an explicit hint or Unicode script inspection."""
    if hint is not None:
        normalized = hint.strip().lower()
        if normalized in SUPPORTED_VOICE_LANGUAGES:
            return normalized
        if normalized not in {"", "auto"}:
            raise VoiceConfigurationError("language must be 'auto', 'en', or 'ar'")
    if isinstance(text, str) and _ARABIC_CHAR_RE.search(text):
        return "ar"
    return "en"


def normalize_stt_language_hint(language: str | None) -> str | None:
    """Validate an optional STT language hint (``None`` / ``'auto'`` / ``'en'`` / ``'ar'``)."""
    if language is None:
        return None
    if not isinstance(language, str):
        raise VoiceAudioValidationError("language hint must be a string")
    normalized = language.strip().lower()
    if not normalized or normalized == "auto":
        return None
    if normalized not in SUPPORTED_VOICE_LANGUAGES:
        raise VoiceAudioValidationError("unsupported language hint")
    return normalized


def sanitize_transcript_text(text: str, *, max_chars: int = VOICE_MAX_TRANSCRIPT_CHARS) -> str:
    """Strip unsafe control characters and bound transcript length."""
    if not isinstance(text, str):
        raise VoiceTranscriptionError("transcription returned non-string text")
    cleaned = _CONTROL_CHARS_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise VoiceTranscriptionError("transcription produced invalid UTF-8") from exc
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
    return cleaned


def sanitize_speech_text(text: str, *, max_chars: int = VOICE_MAX_TTS_CHARS) -> str:
    """Validate and normalize text for local TTS synthesis."""
    if not isinstance(text, str) or "\x00" in text:
        raise VoiceSynthesisError("speech text must be a valid string")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise VoiceSynthesisError("speech text must be valid UTF-8") from exc
    if len(text) > max_chars:
        raise VoiceSynthesisError(f"speech text exceeds {max_chars} character limit")
    cleaned = _CITATION_TAG_RE.sub("", text)
    cleaned = _CONTROL_CHARS_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        raise VoiceSynthesisError("speech text is empty")
    return cleaned


def wrap_pcm16_as_wav(
    pcm_bytes: bytes,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> bytes:
    """Wrap raw little-endian 16-bit PCM bytes into an in-memory WAV container."""
    if not isinstance(pcm_bytes, (bytes, bytearray)):
        raise VoiceAudioValidationError("PCM data must be bytes")
    if not VOICE_MIN_SAMPLE_RATE <= sample_rate <= VOICE_MAX_SAMPLE_RATE:
        raise VoiceAudioValidationError("sample rate out of bounds")
    if not 1 <= channels <= VOICE_MAX_CHANNELS:
        raise VoiceAudioValidationError("channel count out of bounds")
    frame_size = 2 * channels
    if len(pcm_bytes) == 0 or len(pcm_bytes) % frame_size != 0:
        raise VoiceAudioValidationError("PCM payload is empty or misaligned")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(pcm_bytes))
    return buffer.getvalue()


def validate_wav_audio(
    audio_bytes: bytes,
    *,
    max_bytes: int = VOICE_MAX_AUDIO_BYTES,
    min_seconds: float = VOICE_MIN_AUDIO_SECONDS,
    max_seconds: float = VOICE_MAX_AUDIO_SECONDS,
) -> ValidatedWavAudio:
    """Validate uncompressed PCM16 WAV audio completely in memory."""
    if not isinstance(audio_bytes, (bytes, bytearray)):
        raise VoiceAudioValidationError("audio payload must be bytes")
    raw = bytes(audio_bytes)
    byte_length = len(raw)
    if byte_length < 44:
        raise VoiceAudioValidationError("audio payload is too short to be valid WAV")
    if byte_length > max_bytes:
        raise VoiceAudioValidationError("audio payload exceeds maximum byte limit")
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise VoiceAudioValidationError("audio payload must be a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav_file:
            comptype = wav_file.getcomptype()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            declared_frames = wav_file.getnframes()
            if comptype != "NONE":
                raise VoiceAudioValidationError("only uncompressed PCM WAV audio is supported")
            if sample_width != 2:
                raise VoiceAudioValidationError("only 16-bit PCM WAV audio is supported")
            if not 1 <= channels <= VOICE_MAX_CHANNELS:
                raise VoiceAudioValidationError("audio channel count out of bounds")
            if not VOICE_MIN_SAMPLE_RATE <= sample_rate <= VOICE_MAX_SAMPLE_RATE:
                raise VoiceAudioValidationError("audio sample rate out of bounds")
            if declared_frames <= 0:
                raise VoiceAudioValidationError("audio contains no frames")
            expected_frame_bytes = declared_frames * channels * sample_width
            if expected_frame_bytes > max_bytes:
                raise VoiceAudioValidationError("declared audio frame data exceeds maximum byte limit")
            frames = wav_file.readframes(declared_frames)
    except VoiceAudioValidationError:
        raise
    except (wave.Error, EOFError, OSError, struct.error) as exc:
        raise VoiceAudioValidationError("malformed WAV audio") from exc

    frame_size = channels * sample_width
    actual_frames = len(frames) // frame_size
    if actual_frames != declared_frames or len(frames) % frame_size != 0:
        raise VoiceAudioValidationError("truncated or corrupt WAV frame data")
    duration_seconds = actual_frames / float(sample_rate)
    if not math.isfinite(duration_seconds):
        raise VoiceAudioValidationError("invalid audio duration")
    if duration_seconds < min_seconds:
        raise VoiceAudioValidationError("audio clip is too short")
    if duration_seconds > max_seconds:
        raise VoiceAudioValidationError("audio clip exceeds maximum duration")
    return ValidatedWavAudio(
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        frame_count=actual_frames,
        duration_seconds=duration_seconds,
        byte_length=byte_length,
    )


def _require_local_directory(path_value: str | Path, *, label: str) -> Path:
    if not isinstance(path_value, (str, Path)) or not str(path_value).strip():
        raise VoiceConfigurationError(f"{label} must be an explicit local directory path")
    raw = str(path_value).strip()
    if "://" in raw:
        raise VoiceConfigurationError(f"{label} must be a local filesystem path, not a URL")
    resolved = Path(raw).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
    return resolved


def _require_local_file(path_value: str | Path, *, label: str) -> Path:
    if not isinstance(path_value, (str, Path)) or not str(path_value).strip():
        raise VoiceConfigurationError(f"{label} must be an explicit local file path")
    raw = str(path_value).strip()
    if "://" in raw:
        raise VoiceConfigurationError(f"{label} must be a local filesystem path, not a URL")
    resolved = Path(raw).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
    return resolved


class SpeechToTextProvider(Protocol):
    """Provider-neutral local speech-to-text interface."""

    backend_name: str

    def is_ready(self) -> bool: ...

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult: ...


class TextToSpeechProvider(Protocol):
    """Provider-neutral local text-to-speech interface."""

    backend_name: str
    supported_languages: frozenset[str]

    def is_ready(self) -> bool: ...

    def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
    ) -> SynthesisResult: ...


class FasterWhisperSTTAdapter:
    """Offline faster-whisper STT adapter with strict no-download guarantees.

    Requires an explicit local CTranslate2 Whisper model directory containing
    ``model.bin`` and ``config.json`` and always passes ``local_files_only=True``.
    """

    backend_name = "faster-whisper"

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "auto",
        compute_type: str = "default",
        beam_size: int = 3,
        vad_filter: bool = True,
        max_audio_bytes: int = VOICE_MAX_AUDIO_BYTES,
        max_audio_seconds: float = VOICE_MAX_AUDIO_SECONDS,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_path = _require_local_directory(model_path, label="stt model_path")
        if device not in SUPPORTED_STT_DEVICES:
            raise VoiceConfigurationError(f"unsupported stt device: {device}")
        if compute_type not in SUPPORTED_STT_COMPUTE_TYPES:
            raise VoiceConfigurationError(f"unsupported stt compute_type: {compute_type}")
        if type(beam_size) is not int or not 1 <= beam_size <= 5:
            raise VoiceConfigurationError("beam_size must be an integer between 1 and 5")
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = bool(vad_filter)
        self.max_audio_bytes = max_audio_bytes
        self.max_audio_seconds = max_audio_seconds
        self._model_factory = model_factory
        self._model: Any = None

    def _model_files_present(self) -> bool:
        return (
            self.model_path.is_dir()
            and (self.model_path / "model.bin").is_file()
            and (self.model_path / "config.json").is_file()
        )

    def is_ready(self) -> bool:
        if not self._model_files_present():
            return False
        if self._model is not None or self._model_factory is not None:
            return True
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return False
        return True

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._model_files_present():
            raise VoiceModelUnavailableError(
                "Configured faster-whisper local model directory is missing model.bin or config.json"
            )
        factory = self._model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                raise VoiceModelUnavailableError(
                    "faster-whisper is not installed in the active Python environment"
                ) from exc
            factory = WhisperModel
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            self._model = factory(
                str(self.model_path),
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceModelUnavailableError("Failed to initialize local faster-whisper model") from exc
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        validated = validate_wav_audio(
            audio_bytes,
            max_bytes=self.max_audio_bytes,
            max_seconds=self.max_audio_seconds,
        )
        lang_hint = normalize_stt_language_hint(language)
        model = self._load_model()
        try:
            segments, info = model.transcribe(
                io.BytesIO(bytes(audio_bytes)),
                language=lang_hint,
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            parts: list[str] = []
            for segment in segments:
                piece = getattr(segment, "text", "")
                if isinstance(piece, str) and piece.strip():
                    parts.append(piece.strip())
            combined = sanitize_transcript_text(" ".join(parts))
            detected = getattr(info, "language", None) if info is not None else None
            resolved_language: str | None = lang_hint
            if isinstance(detected, str) and detected.strip().lower() in SUPPORTED_VOICE_LANGUAGES:
                resolved_language = detected.strip().lower()
            elif combined:
                resolved_language = detect_voice_language(combined, lang_hint)
        except VoiceError:
            raise
        except TimeoutError as exc:
            raise VoiceTimeoutError("Local speech transcription timed out") from exc
        except Exception as exc:
            raise VoiceTranscriptionError("Local speech transcription failed") from exc
        return TranscriptionResult(
            text=combined,
            language=resolved_language,
            duration_seconds=validated.duration_seconds,
            backend=self.backend_name,
        )


class ChatterboxMultilingualTTSAdapter:
    """Offline Chatterbox Multilingual TTS adapter (English & Arabic).

    Loads exclusively from an explicit local model directory via ``from_local``
    so Hugging Face network downloads never occur.
    """

    backend_name = "chatterbox-multilingual"
    supported_languages = SUPPORTED_VOICE_LANGUAGES

    def __init__(
        self,
        model_dir: str | Path,
        *,
        device: str = "cuda",
        voice_prompt_path: str | Path | None = None,
        exaggeration: float = 0.35,
        cfg_weight: float = 0.5,
        max_tts_chars: int = VOICE_MAX_TTS_CHARS,
        model_loader: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.model_dir = _require_local_directory(model_dir, label="tts model_dir")
        if device not in SUPPORTED_TTS_DEVICES:
            raise VoiceConfigurationError(f"unsupported tts device: {device}")
        if not 0.0 <= float(exaggeration) <= 1.0:
            raise VoiceConfigurationError("exaggeration must be between 0.0 and 1.0")
        if not 0.0 <= float(cfg_weight) <= 1.0:
            raise VoiceConfigurationError("cfg_weight must be between 0.0 and 1.0")
        self.device = device
        self.voice_prompt_path = (
            _require_local_file(voice_prompt_path, label="tts voice_prompt_path")
            if voice_prompt_path is not None else None
        )
        self.exaggeration = float(exaggeration)
        self.cfg_weight = float(cfg_weight)
        self.max_tts_chars = max_tts_chars
        self._model_loader = model_loader
        self._model: Any = None
        self._inference_lock = threading.Lock()

    def _model_files_present(self) -> bool:
        if not self.model_dir.is_dir():
            return False
        if self.voice_prompt_path is not None and not self.voice_prompt_path.is_file():
            return False
        # chatterbox-tts 0.1.7 from_local requires this complete multilingual bundle.
        required = ("ve.safetensors", "t3_mtl23ls_v2.safetensors", "s3gen.safetensors", "tokenizer.json")
        return all((self.model_dir / name).is_file() for name in required)

    def is_ready(self) -> bool:
        if not self._model_files_present():
            return False
        if self._model is not None or self._model_loader is not None:
            return True
        try:
            import chatterbox.mtl_tts  # noqa: F401
        except Exception:
            return False
        return True

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if not self._model_files_present():
            raise VoiceModelUnavailableError(
                "Configured Chatterbox Multilingual local model directory is incomplete or missing"
            )
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        loader = self._model_loader
        if loader is None:
            try:
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            except Exception as exc:
                raise VoiceModelUnavailableError(
                    "chatterbox-tts is not installed in the active Python environment"
                ) from exc
            loader = lambda path, dev: ChatterboxMultilingualTTS.from_local(path, device=dev)
        try:
            self._model = loader(str(self.model_dir), self.device)
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceModelUnavailableError("Failed to initialize local Chatterbox Multilingual model") from exc
        return self._model

    @staticmethod
    def _samples_to_pcm16(waveform: Any, *, max_samples: int) -> bytes:
        """Convert a 1D/2D float waveform (tensor, ndarray, or sequence) to PCM16 bytes."""
        data = waveform
        if hasattr(data, "numel") and int(data.numel()) > max_samples:
            raise VoiceSynthesisError("Synthesized audio exceeds maximum size limit")
        if hasattr(data, "detach"):
            data = data.detach()
        if hasattr(data, "cpu"):
            data = data.cpu()
        if hasattr(data, "squeeze"):
            data = data.squeeze()
        if hasattr(data, "__len__") and len(data) > max_samples:
            raise VoiceSynthesisError("Synthesized audio exceeds maximum size limit")
        if hasattr(data, "tolist"):
            data = data.tolist()
        if isinstance(data, list) and data and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, (list, tuple)) or not data:
            raise VoiceSynthesisError("Chatterbox model returned empty audio samples")
        pcm = bytearray(len(data) * 2)
        for index, raw_sample in enumerate(data):
            sample = float(raw_sample)
            if not math.isfinite(sample):
                sample = 0.0
            clamped = max(-1.0, min(1.0, sample))
            struct.pack_into("<h", pcm, index * 2, int(round(clamped * 32767.0)))
        return bytes(pcm)

    def synthesize(self, text: str, *, language: str = "en") -> SynthesisResult:
        cleaned = sanitize_speech_text(text, max_chars=self.max_tts_chars)
        lang = detect_voice_language(cleaned, language)
        model = self._load_model()
        try:
            kwargs: dict[str, Any] = {
                "language_id": lang,
                "exaggeration": self.exaggeration,
                "cfg_weight": self.cfg_weight,
            }
            if self.voice_prompt_path is not None:
                kwargs["audio_prompt_path"] = str(self.voice_prompt_path)
            with self._inference_lock:
                waveform = model.generate(cleaned, **kwargs)
            sample_rate = int(getattr(model, "sr", 24000))
            if not VOICE_MIN_SAMPLE_RATE <= sample_rate <= VOICE_MAX_SAMPLE_RATE:
                raise VoiceSynthesisError("Chatterbox returned an unsupported sample rate")
            max_samples = (VOICE_MAX_TTS_AUDIO_BYTES - 44) // 2
            pcm_bytes = self._samples_to_pcm16(waveform, max_samples=max_samples)
            wav_bytes = wrap_pcm16_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
            validated = validate_wav_audio(
                wav_bytes,
                max_bytes=VOICE_MAX_TTS_AUDIO_BYTES,
                min_seconds=0.05,
                max_seconds=120.0,
            )
        except VoiceError:
            raise
        except TimeoutError as exc:
            raise VoiceTimeoutError("Local Chatterbox TTS synthesis timed out") from exc
        except Exception as exc:
            raise VoiceSynthesisError("Local Chatterbox TTS synthesis failed") from exc
        return SynthesisResult(
            audio_bytes=wav_bytes,
            content_type="audio/wav",
            sample_rate=validated.sample_rate,
            duration_seconds=validated.duration_seconds,
            language=lang,
            backend=self.backend_name,
        )


class PiperTTSAdapter:
    """Lightweight offline Piper ONNX TTS adapter.

    Invokes an explicit local ``piper`` executable with explicit local ``.onnx``
    and ``.onnx.json`` voice files using ``--output-raw`` so audio is captured
    purely in memory with zero temp files and zero network access.
    """

    backend_name = "piper"

    def __init__(
        self,
        executable_path: str | Path,
        model_paths: dict[str, str | Path],
        *,
        timeout: float = DEFAULT_VOICE_TIMEOUT_SECONDS,
        max_tts_chars: int = VOICE_MAX_TTS_CHARS,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.executable_path = _require_local_file(executable_path, label="piper executable_path")
        if not isinstance(model_paths, dict) or not model_paths:
            raise VoiceConfigurationError("piper requires at least one explicit local voice model")
        resolved_models: dict[str, Path] = {}
        for lang_key, path_val in model_paths.items():
            normalized = str(lang_key).strip().lower()
            if normalized not in SUPPORTED_VOICE_LANGUAGES:
                raise VoiceConfigurationError(f"unsupported piper language key: {lang_key}")
            resolved_models[normalized] = _require_local_file(
                path_val, label=f"piper model_path[{normalized}]"
            )
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1.0 <= timeout <= 180.0:
            raise VoiceConfigurationError("timeout must be between 1 and 180 seconds")
        self.model_paths = resolved_models
        self.supported_languages = frozenset(resolved_models.keys())
        self.timeout = float(timeout)
        self.max_tts_chars = max_tts_chars
        self._runner = runner
        self._inference_lock = threading.Lock()

    def _run_bounded(self, argv: list[str], input_bytes: bytes) -> subprocess.CompletedProcess:
        """Capture Piper output incrementally and terminate once its PCM budget is exceeded."""
        if self._runner is not None:
            return self._runner(argv, input=input_bytes, capture_output=True,
                                timeout=self.timeout, check=False, shell=False)
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, shell=False)
        timer = threading.Timer(self.timeout, process.kill)
        timer.daemon = True
        timer.start()
        output = bytearray()
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(input_bytes)
            process.stdin.close()
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                if len(output) + len(chunk) > VOICE_MAX_TTS_AUDIO_BYTES - 44:
                    process.kill()
                    raise VoiceSynthesisError("Synthesized audio exceeds maximum size limit")
                output.extend(chunk)
            returncode = process.wait(timeout=max(0.1, self.timeout))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise VoiceTimeoutError("Local Piper TTS synthesis timed out") from exc
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
        return subprocess.CompletedProcess(argv, returncode, stdout=bytes(output), stderr=b"")

    @staticmethod
    def _config_path_for(model_path: Path) -> Path:
        return Path(f"{model_path}.json")

    def _model_ready_for(self, lang: str) -> bool:
        model_path = self.model_paths.get(lang)
        if model_path is None:
            return False
        return (
            self.executable_path.is_file()
            and model_path.is_file()
            and self._config_path_for(model_path).is_file()
        )

    def is_ready(self) -> bool:
        return any(self._model_ready_for(lang) for lang in self.model_paths)

    def _sample_rate_for(self, config_path: Path) -> int:
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            rate = int(payload.get("audio", {}).get("sample_rate", 22050))
        except Exception:
            rate = 22050
        if not VOICE_MIN_SAMPLE_RATE <= rate <= VOICE_MAX_SAMPLE_RATE:
            return 22050
        return rate

    def synthesize(self, text: str, *, language: str = "en") -> SynthesisResult:
        cleaned = sanitize_speech_text(text, max_chars=self.max_tts_chars)
        lang = detect_voice_language(cleaned, language)
        if lang not in self.model_paths:
            raise VoiceModelUnavailableError(f"No local Piper voice model configured for language '{lang}'")
        if not self._model_ready_for(lang):
            raise VoiceModelUnavailableError(
                "Configured Piper executable or local ONNX voice files are missing"
            )
        model_path = self.model_paths[lang]
        config_path = self._config_path_for(model_path)
        sample_rate = self._sample_rate_for(config_path)
        argv = [
            str(self.executable_path),
            "--model",
            str(model_path),
            "--config",
            str(config_path),
            "--output-raw",
        ]
        try:
            with self._inference_lock:
                completed = self._run_bounded(argv, cleaned.encode("utf-8"))
        except subprocess.TimeoutExpired as exc:
            raise VoiceTimeoutError("Local Piper TTS synthesis timed out") from exc
        except OSError as exc:
            raise VoiceModelUnavailableError("Failed to launch local Piper executable") from exc
        if completed.returncode != 0:
            raise VoiceSynthesisError("Local Piper TTS process exited with an error")
        stdout = completed.stdout or b""
        if len(stdout) > VOICE_MAX_TTS_AUDIO_BYTES - 44:
            raise VoiceSynthesisError("Synthesized audio exceeds maximum size limit")
        pcm_bytes = bytes(stdout)
        if not pcm_bytes:
            raise VoiceSynthesisError("Local Piper TTS returned empty audio")
        wav_bytes = wrap_pcm16_as_wav(pcm_bytes, sample_rate=sample_rate, channels=1)
        validated = validate_wav_audio(
            wav_bytes,
            max_bytes=VOICE_MAX_TTS_AUDIO_BYTES,
            min_seconds=0.05,
            max_seconds=120.0,
        )
        return SynthesisResult(
            audio_bytes=wav_bytes,
            content_type="audio/wav",
            sample_rate=validated.sample_rate,
            duration_seconds=validated.duration_seconds,
            language=lang,
            backend=self.backend_name,
        )


class VoiceService:
    """Coordinator for push-to-talk local STT, local TTS, and Jarvis persona."""

    def __init__(
        self,
        *,
        stt: SpeechToTextProvider | None = None,
        tts: TextToSpeechProvider | None = None,
        persona: str = "default",
        max_audio_bytes: int = VOICE_MAX_AUDIO_BYTES,
        max_audio_seconds: float = VOICE_MAX_AUDIO_SECONDS,
        max_tts_chars: int = VOICE_MAX_TTS_CHARS,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self.persona = validate_persona_name(persona)
        if type(max_audio_bytes) is not int or not 1024 <= max_audio_bytes <= VOICE_MAX_AUDIO_BYTES:
            raise VoiceConfigurationError(f"max_audio_bytes must be between 1024 and {VOICE_MAX_AUDIO_BYTES}")
        if not isinstance(max_audio_seconds, (int, float)) or not 1.0 <= float(max_audio_seconds) <= VOICE_MAX_AUDIO_SECONDS:
            raise VoiceConfigurationError(f"max_audio_seconds must be between 1 and {VOICE_MAX_AUDIO_SECONDS}")
        if type(max_tts_chars) is not int or not 64 <= max_tts_chars <= VOICE_MAX_TTS_CHARS:
            raise VoiceConfigurationError(f"max_tts_chars must be between 64 and {VOICE_MAX_TTS_CHARS}")
        self.max_audio_bytes = max_audio_bytes
        self.max_audio_seconds = float(max_audio_seconds)
        self.max_tts_chars = max_tts_chars

    def status_payload(self) -> dict[str, Any]:
        return {
            "mode": "push_to_talk",
            "stt_configured": self.stt is not None,
            "stt_ready": bool(self.stt.is_ready()) if self.stt is not None else False,
            "stt_backend": self.stt.backend_name if self.stt is not None else None,
            "tts_configured": self.tts is not None,
            "tts_ready": bool(self.tts.is_ready()) if self.tts is not None else False,
            "tts_backend": self.tts.backend_name if self.tts is not None else None,
            "tts_languages": sorted(self.tts.supported_languages) if self.tts is not None else [],
            "languages": sorted(SUPPORTED_VOICE_LANGUAGES),
            "persona": self.persona,
            "max_audio_bytes": self.max_audio_bytes,
            "max_audio_seconds": self.max_audio_seconds,
            "max_tts_chars": self.max_tts_chars,
            "raw_audio_stored": False,
            "transcripts_stored": False,
            "browser_speech_fallback": False,
        }

    def system_prompt_for_chat(self, base_prompt: str, *, voice_mode: bool = False) -> str:
        return resolve_chat_system_prompt(base_prompt, persona=self.persona, voice_mode=voice_mode)

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        if self.stt is None:
            raise VoiceModelUnavailableError("No local STT backend is configured")
        validate_wav_audio(
            audio_bytes,
            max_bytes=self.max_audio_bytes,
            max_seconds=self.max_audio_seconds,
        )
        lang_hint = normalize_stt_language_hint(language)
        return self.stt.transcribe(audio_bytes, language=lang_hint)

    def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
    ) -> SynthesisResult:
        if self.tts is None:
            raise VoiceModelUnavailableError("No local TTS backend is configured")
        cleaned = sanitize_speech_text(text, max_chars=self.max_tts_chars)
        lang = detect_voice_language(cleaned, language)
        return self.tts.synthesize(cleaned, language=lang)


def build_voice_service_from_options(
    *,
    persona: str = "default",
    stt_backend: str = "none",
    stt_model_path: str | Path | None = None,
    stt_device: str = "auto",
    stt_compute_type: str = "default",
    tts_backend: str = "none",
    tts_model_path: str | Path | None = None,
    tts_model_ar_path: str | Path | None = None,
    tts_binary_path: str | Path | None = None,
    tts_voice_ref: str | Path | None = None,
    tts_device: str = "cuda",
) -> VoiceService:
    """Construct a VoiceService from explicit CLI/server options (fail-closed)."""
    stt_provider: SpeechToTextProvider | None = None
    tts_provider: TextToSpeechProvider | None = None

    if stt_backend == "faster-whisper":
        if stt_model_path is None:
            raise VoiceConfigurationError("--stt-model-path is required when --stt-backend=faster-whisper")
        stt_provider = FasterWhisperSTTAdapter(
            stt_model_path,
            device=stt_device,
            compute_type=stt_compute_type,
        )
    elif stt_backend != "none":
        raise VoiceConfigurationError(f"unsupported stt backend: {stt_backend}")

    if tts_backend == "chatterbox":
        if tts_model_path is None:
            raise VoiceConfigurationError("--tts-model-path is required when --tts-backend=chatterbox")
        tts_provider = ChatterboxMultilingualTTSAdapter(
            tts_model_path,
            device=tts_device,
            voice_prompt_path=tts_voice_ref,
        )
    elif tts_backend == "piper":
        if tts_binary_path is None:
            raise VoiceConfigurationError("--tts-binary-path is required when --tts-backend=piper")
        if tts_model_path is None and tts_model_ar_path is None:
            raise VoiceConfigurationError(
                "at least one local ONNX voice (--tts-model-path or --tts-model-ar-path) is required for piper"
            )
        models: dict[str, str | Path] = {}
        if tts_model_path is not None:
            models["en"] = tts_model_path
        if tts_model_ar_path is not None:
            models["ar"] = tts_model_ar_path
        tts_provider = PiperTTSAdapter(tts_binary_path, models)
    elif tts_backend != "none":
        raise VoiceConfigurationError(f"unsupported tts backend: {tts_backend}")

    return VoiceService(stt=stt_provider, tts=tts_provider, persona=persona)

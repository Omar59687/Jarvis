"""Unit tests for jarvis_core.voice (M03-C1 offline local voice foundation)."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from jarvis_core.voice import (
    ChatterboxMultilingualTTSAdapter,
    FasterWhisperSTTAdapter,
    PiperTTSAdapter,
    VoiceAudioValidationError,
    VoiceConfigurationError,
    VoiceModelUnavailableError,
    VoiceService,
    VoiceSynthesisError,
    VoiceTimeoutError,
    VoiceTranscriptionError,
    build_voice_service_from_options,
    detect_voice_language,
    resolve_chat_system_prompt,
    sanitize_speech_text,
    sanitize_transcript_text,
    validate_wav_audio,
    wrap_pcm16_as_wav,
)


def make_test_wav(
    duration_seconds: float = 0.5,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    freq: float = 440.0,
) -> bytes:
    frame_count = max(1, int(round(duration_seconds * sample_rate)))
    samples = bytearray()
    for i in range(frame_count):
        val = int(round(12000 * math.sin(2.0 * math.pi * freq * i / sample_rate)))
        for _ in range(channels):
            samples.extend(struct.pack("<h", val))
    return wrap_pcm16_as_wav(bytes(samples), sample_rate=sample_rate, channels=channels)


class WavValidationTests(unittest.TestCase):
    def test_valid_mono_and_stereo_pcm16_wav_pass(self) -> None:
        wav_mono = make_test_wav(0.5, sample_rate=16000, channels=1)
        info = validate_wav_audio(wav_mono)
        self.assertEqual(info.sample_rate, 16000)
        self.assertEqual(info.channels, 1)
        self.assertEqual(info.sample_width, 2)
        self.assertAlmostEqual(info.duration_seconds, 0.5, places=3)

        wav_stereo = make_test_wav(0.25, sample_rate=44100, channels=2)
        info2 = validate_wav_audio(wav_stereo)
        self.assertEqual(info2.channels, 2)
        self.assertEqual(info2.sample_rate, 44100)

    def test_invalid_oversized_short_long_and_corrupt_wav_rejected(self) -> None:
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(b"not a wav file at all" * 5)
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(make_test_wav(0.02))  # too short (<0.1s)
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(make_test_wav(2.0), max_seconds=1.0)
        wav = make_test_wav(0.5)
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(wav, max_bytes=100)
        # Truncated frame payload must fail
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(wav[:-20])
        # 8-bit PCM WAV must fail (only PCM16 allowed)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1)
            wf.setframerate(16000)
            wf.writeframes(b"\x80" * 4000)
        with self.assertRaises(VoiceAudioValidationError):
            validate_wav_audio(buf.getvalue())


class PersonaAndSanitizationTests(unittest.TestCase):
    def test_default_persona_preserves_base_prompt_verbatim(self) -> None:
        base = "You have no access to the user's memories."
        self.assertEqual(resolve_chat_system_prompt(base, persona="default", voice_mode=False), base)

    def test_jarvis_persona_preserves_safety_and_adds_bilingual_butler_guidance(self) -> None:
        base = "You have no access to the user's memories."
        prompt = resolve_chat_system_prompt(base, persona="jarvis", voice_mode=False)
        self.assertTrue(prompt.startswith(base))
        self.assertIn("British-butler", prompt)
        self.assertIn("sir", prompt)
        self.assertIn("Arabic", prompt)
        self.assertIn("Never claim to have executed tasks", prompt)
        self.assertIn("original voice identity", prompt)

        voice_prompt = resolve_chat_system_prompt(base, persona="default", voice_mode=True)
        self.assertIn("British-butler", voice_prompt)

    def test_invalid_persona_rejected(self) -> None:
        with self.assertRaises(VoiceConfigurationError):
            resolve_chat_system_prompt("base", persona="pirate")

    def test_language_detection_and_text_sanitization(self) -> None:
        self.assertEqual(detect_voice_language("Hello Jarvis"), "en")
        self.assertEqual(detect_voice_language("مرحبا جارفس"), "ar")
        self.assertEqual(detect_voice_language("Hello", "ar"), "ar")
        self.assertEqual(sanitize_transcript_text("  Hello\x07\x00  world \n "), "Hello world")
        self.assertEqual(sanitize_speech_text("Answer [D1] and [M2] here."), "Answer and here.")
        with self.assertRaises(VoiceSynthesisError):
            sanitize_speech_text("   ")
        with self.assertRaises(VoiceSynthesisError):
            sanitize_speech_text("x" * 2001)


class FasterWhisperAdapterTests(unittest.TestCase):
    def test_missing_local_model_directory_fails_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = Path(tmp) / "missing-whisper"
            adapter = FasterWhisperSTTAdapter(missing_dir)
            self.assertFalse(adapter.is_ready())
            with self.assertRaises(VoiceModelUnavailableError):
                adapter.transcribe(make_test_wav(0.3))

    def test_rejects_url_model_path(self) -> None:
        with self.assertRaises(VoiceConfigurationError):
            FasterWhisperSTTAdapter("https://huggingface.co/Systran/faster-whisper-small")

    def test_enforces_local_files_only_and_in_memory_bytesio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "whisper-small"
            model_dir.mkdir()
            (model_dir / "model.bin").write_bytes(b"fake-weights")
            (model_dir / "config.json").write_text("{}", encoding="utf-8")

            calls: list[dict] = []

            class FakeSegment:
                def __init__(self, text: str):
                    self.text = text

            class FakeInfo:
                def __init__(self, language: str):
                    self.language = language

            class FakeWhisperModel:
                def __init__(self, path, *, device, compute_type, local_files_only):
                    calls.append({
                        "init": (path, device, compute_type, local_files_only),
                    })

                def transcribe(self, audio_source, *, language, beam_size, vad_filter):
                    calls.append({
                        "is_bytesio": isinstance(audio_source, io.BytesIO),
                        "language": language,
                        "beam_size": beam_size,
                        "vad_filter": vad_filter,
                    })
                    if language == "ar":
                        return [FakeSegment("مرحبا جارفس")], FakeInfo("ar")
                    return [FakeSegment("Good evening"), FakeSegment("Jarvis.")], FakeInfo("en")

            adapter = FasterWhisperSTTAdapter(
                model_dir,
                device="cpu",
                compute_type="int8",
                model_factory=FakeWhisperModel,
            )
            self.assertTrue(adapter.is_ready())
            before_files = set(model_dir.iterdir())

            res_en = adapter.transcribe(make_test_wav(0.4), language="auto")
            self.assertEqual(res_en.text, "Good evening Jarvis.")
            self.assertEqual(res_en.language, "en")
            self.assertEqual(res_en.backend, "faster-whisper")
            self.assertEqual(calls[0]["init"][3], True)  # local_files_only=True
            self.assertTrue(calls[1]["is_bytesio"])
            self.assertIsNone(calls[1]["language"])

            res_ar = adapter.transcribe(make_test_wav(0.4), language="ar")
            self.assertEqual(res_ar.text, "مرحبا جارفس")
            self.assertEqual(res_ar.language, "ar")
            # Zero files written to model_dir
            self.assertEqual(set(model_dir.iterdir()), before_files)

    def test_transcription_error_and_timeout_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "whisper-small"
            model_dir.mkdir()
            (model_dir / "model.bin").write_bytes(b"fake")
            (model_dir / "config.json").write_text("{}", encoding="utf-8")

            class FailingModel:
                def __init__(self, *args, **kwargs):
                    self.mode = "error"

                def transcribe(self, *args, **kwargs):
                    if self.mode == "timeout":
                        raise TimeoutError("slow")
                    raise RuntimeError("secret C:\\private\\path error")

            model_instance = FailingModel()
            adapter = FasterWhisperSTTAdapter(model_dir, model_factory=lambda *a, **k: model_instance)
            with self.assertRaises(VoiceTranscriptionError):
                adapter.transcribe(make_test_wav(0.3))
            model_instance.mode = "timeout"
            with self.assertRaises(VoiceTimeoutError):
                adapter.transcribe(make_test_wav(0.3))


class LocalTTSAdaptersTests(unittest.TestCase):
    def test_chatterbox_requires_local_files_and_synthesizes_en_and_ar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "chatterbox-mtl"
            adapter_missing = ChatterboxMultilingualTTSAdapter(model_dir, device="cpu")
            self.assertFalse(adapter_missing.is_ready())
            with self.assertRaises(VoiceModelUnavailableError):
                adapter_missing.synthesize("Hello", language="en")

            model_dir.mkdir()
            (model_dir / "ve.safetensors").write_bytes(b"fake")
            voice_ref = model_dir / "jarvis_ref.wav"
            voice_ref.write_bytes(make_test_wav(0.2))

            calls: list[tuple] = []

            class FakeChatterbox:
                sr = 24000

                def generate(self, text, **kwargs):
                    calls.append((text, kwargs))
                    return [0.1 * math.sin(i / 20.0) for i in range(4800)]

            adapter = ChatterboxMultilingualTTSAdapter(
                model_dir,
                device="cpu",
                voice_prompt_path=voice_ref,
                model_loader=lambda path, dev: FakeChatterbox(),
            )
            self.assertTrue(adapter.is_ready())
            res_en = adapter.synthesize("Very good, sir.", language="en")
            self.assertEqual(res_en.content_type, "audio/wav")
            self.assertEqual(res_en.language, "en")
            self.assertEqual(res_en.backend, "chatterbox-multilingual")
            self.assertEqual(res_en.audio_bytes[:4], b"RIFF")
            self.assertEqual(calls[0][1]["language_id"], "en")
            self.assertEqual(calls[0][1]["audio_prompt_path"], str(voice_ref.resolve()))

            res_ar = adapter.synthesize("على الرحب والسعة.", language="ar")
            self.assertEqual(res_ar.language, "ar")
            self.assertEqual(calls[1][1]["language_id"], "ar")

    def test_piper_adapter_runs_offline_in_memory_and_enforces_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "piper.exe"
            en_onnx = root / "en_GB-alan.onnx"
            en_json = root / "en_GB-alan.onnx.json"
            ar_onnx = root / "ar_JO-kareem.onnx"
            ar_json = root / "ar_JO-kareem.onnx.json"

            # Missing binary/model fails closed
            adapter = PiperTTSAdapter(exe, {"en": en_onnx, "ar": ar_onnx})
            self.assertFalse(adapter.is_ready())
            with self.assertRaises(VoiceModelUnavailableError):
                adapter.synthesize("Hello", language="en")

            exe.write_bytes(b"fake-bin")
            en_onnx.write_bytes(b"fake-en")
            en_json.write_text(json.dumps({"audio": {"sample_rate": 22050}}), encoding="utf-8")
            ar_onnx.write_bytes(b"fake-ar")
            ar_json.write_text(json.dumps({"audio": {"sample_rate": 22050}}), encoding="utf-8")

            invocations: list[dict] = []

            def fake_runner(argv, *, input, capture_output, timeout, check, shell):
                invocations.append({
                    "argv": argv,
                    "input": input,
                    "shell": shell,
                    "timeout": timeout,
                })
                # 0.25s of 22050 Hz PCM16 silence/tone
                pcm = struct.pack("<h", 1000) * 5512
                return subprocess.CompletedProcess(argv, 0, stdout=pcm, stderr=b"")

            adapter = PiperTTSAdapter(exe, {"en": en_onnx, "ar": ar_onnx}, runner=fake_runner)
            self.assertTrue(adapter.is_ready())
            res_en = adapter.synthesize("Right away, sir.", language="en")
            self.assertEqual(res_en.backend, "piper")
            self.assertEqual(res_en.sample_rate, 22050)
            self.assertEqual(res_en.audio_bytes[:4], b"RIFF")
            self.assertFalse(invocations[0]["shell"])
            self.assertIn("--output-raw", invocations[0]["argv"])

            res_ar = adapter.synthesize("أهلاً بك.", language="ar")
            self.assertEqual(res_ar.language, "ar")
            self.assertIn(str(ar_onnx.resolve()), invocations[1]["argv"])

            # Timeout and non-zero exit handling
            def timeout_runner(*args, **kwargs):
                raise subprocess.TimeoutExpired(cmd="piper", timeout=5)

            adapter_timeout = PiperTTSAdapter(exe, {"en": en_onnx}, runner=timeout_runner)
            with self.assertRaises(VoiceTimeoutError):
                adapter_timeout.synthesize("Hello", language="en")

            def fail_runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"boom")

            adapter_fail = PiperTTSAdapter(exe, {"en": en_onnx}, runner=fail_runner)
            with self.assertRaises(VoiceSynthesisError):
                adapter_fail.synthesize("Hello", language="en")

    def test_build_voice_service_options_fail_closed(self) -> None:
        with self.assertRaises(VoiceConfigurationError):
            build_voice_service_from_options(stt_backend="faster-whisper", stt_model_path=None)
        with self.assertRaises(VoiceConfigurationError):
            build_voice_service_from_options(tts_backend="chatterbox", tts_model_path=None)
        with self.assertRaises(VoiceConfigurationError):
            build_voice_service_from_options(tts_backend="piper", tts_binary_path=None, tts_model_path="a.onnx")
        with self.assertRaises(VoiceConfigurationError):
            build_voice_service_from_options(tts_backend="piper", tts_binary_path="piper.exe")

        svc = build_voice_service_from_options(persona="jarvis")
        status = svc.status_payload()
        self.assertEqual(status["mode"], "push_to_talk")
        self.assertEqual(status["persona"], "jarvis")
        self.assertFalse(status["stt_configured"])
        self.assertFalse(status["tts_configured"])
        self.assertFalse(status["raw_audio_stored"])
        self.assertFalse(status["transcripts_stored"])
        self.assertFalse(status["browser_speech_fallback"])


if __name__ == "__main__":
    unittest.main()

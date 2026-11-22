"""HTTP, security, privacy, and integration tests for Jarvis M03-C1 local voice."""
from __future__ import annotations

import http.client
import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import unittest

from jarvis_core.model_provider import GenerationResult
from jarvis_core.storage import Database, Permission, WorkspaceKind
from jarvis_core.voice import (
    SynthesisResult,
    TranscriptionResult,
    VoiceModelUnavailableError,
    VoiceService,
    VoiceSynthesisError,
    VoiceTimeoutError,
    VoiceTranscriptionError,
    wrap_pcm16_as_wav,
)
from prototypes.m03_b1.service import (
    API_PREFIX,
    GENERAL_CHAT_SYSTEM_PROMPT,
    MAX_VOICE_OPS_PER_MINUTE,
    make_server,
)


MODEL = "local-voice-test-model"


def make_wav_bytes(duration_seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
    frames = max(1, int(round(duration_seconds * sample_rate)))
    pcm = bytearray()
    for i in range(frames):
        pcm.extend(struct.pack("<h", int(round(8000 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)))))
    return wrap_pcm16_as_wav(bytes(pcm), sample_rate=sample_rate, channels=1)


class FakeGenerator:
    def __init__(self, reply: str = "At your service, sir."):
        self.reply = reply
        self.calls: list[tuple] = []

    def generate(self, prompt: str, model: str, *, system_prompt: str | None = None,
                 temperature: float = 0.3, max_output_tokens: int = 192):
        self.calls.append((prompt, model, system_prompt, max_output_tokens))
        return GenerationResult(model=model, text=self.reply, done=True)


class FakeSTT:
    backend_name = "faster-whisper"

    def __init__(self):
        self.ready = True
        self.error: Exception | None = None
        self.result_text = "What is the status?"
        self.result_lang = "en"
        self.calls: list[tuple[int, str | None]] = []
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()

    def is_ready(self) -> bool:
        return self.ready

    def transcribe(self, audio_bytes: bytes, *, language: str | None = None) -> TranscriptionResult:
        self.calls.append((len(audio_bytes), language))
        if self.block:
            self.started.set()
            self.release.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return TranscriptionResult(
            text=self.result_text,
            language=language or self.result_lang,
            duration_seconds=0.4,
            backend=self.backend_name,
        )


class FakeTTS:
    backend_name = "piper"
    supported_languages = frozenset({"en", "ar"})

    def __init__(self):
        self.ready = True
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def is_ready(self) -> bool:
        return self.ready

    def synthesize(self, text: str, *, language: str = "en") -> SynthesisResult:
        self.calls.append((text, language))
        if self.error is not None:
            raise self.error
        wav = make_wav_bytes(0.3, sample_rate=22050)
        return SynthesisResult(
            audio_bytes=wav,
            content_type="audio/wav",
            sample_rate=22050,
            duration_seconds=0.3,
            language=language,
            backend=self.backend_name,
        )


class LocalVoiceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.generator = FakeGenerator()
        self.stt = FakeSTT()
        self.tts = FakeTTS()
        self.voice_service = VoiceService(stt=self.stt, tts=self.tts, persona="default")
        self.server = make_server(
            0,
            provider="test",
            model=MODEL,
            generator=self.generator,
            pairing_code="voice-pair-secret",
            voice_service=self.voice_service,
            clock=lambda: self.now,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def raw_request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        server=None,
    ) -> tuple[int, dict[str, str], bytes]:
        srv = server or self.server
        conn = http.client.HTTPConnection(srv.server_address[0], srv.server_address[1], timeout=5)
        try:
            req_headers = {"Host": srv.expected_host}
            if method in {"POST", "PUT", "DELETE"}:
                req_headers["Origin"] = srv.expected_origin
            if headers:
                req_headers.update(headers)
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def pair(self, server=None, code: str = "voice-pair-secret") -> tuple[str, str]:
        payload = json.dumps({"pairing_code": code}).encode("utf-8")
        status, headers, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/session/pair",
            body=payload,
            headers={"Content-Type": "application/json"},
            server=server,
        )
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        csrf = json.loads(body)["csrf_token"]
        return cookie, csrf

    def test_voice_status_requires_auth_and_reports_offline_posture(self) -> None:
        status, _, body = self.raw_request("GET", f"{API_PREFIX}/voice/status")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body), {"error": "authentication_required"})

        cookie, _ = self.pair()
        # Foreign Origin rejected even on GET
        status, _, body = self.raw_request(
            "GET",
            f"{API_PREFIX}/voice/status",
            headers={"Cookie": cookie, "Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "origin_not_allowed"})

        status, headers, body = self.raw_request(
            "GET",
            f"{API_PREFIX}/voice/status",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn("media-src 'self' blob:", headers.get("Content-Security-Policy", ""))
        payload = json.loads(body)
        self.assertEqual(payload["mode"], "push_to_talk")
        self.assertTrue(payload["stt_configured"])
        self.assertTrue(payload["stt_ready"])
        self.assertEqual(payload["stt_backend"], "faster-whisper")
        self.assertTrue(payload["tts_configured"])
        self.assertTrue(payload["tts_ready"])
        self.assertEqual(payload["tts_backend"], "piper")
        self.assertEqual(payload["languages"], ["ar", "en"])
        self.assertFalse(payload["raw_audio_stored"])
        self.assertFalse(payload["transcripts_stored"])
        self.assertFalse(payload["browser_speech_fallback"])

    def test_transcribe_security_validation_errors_and_bilingual_success(self) -> None:
        wav = make_wav_bytes(0.4)
        # Unauthenticated rejected before reading oversized body
        conn = http.client.HTTPConnection(self.server.server_address[0], self.server.server_address[1], timeout=5)
        try:
            conn.putrequest("POST", f"{API_PREFIX}/voice/transcribe", skip_host=True)
            conn.putheader("Host", self.server.expected_host)
            conn.putheader("Origin", self.server.expected_origin)
            conn.putheader("Content-Type", "audio/wav")
            conn.putheader("Content-Length", "99999999")
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 401)
            self.assertEqual(json.loads(resp.read()), {"error": "authentication_required"})
        finally:
            conn.close()

        cookie, csrf = self.pair()
        # Missing CSRF rejected
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body), {"error": "csrf_rejected"})

        # Wrong Content-Type rejected
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/webm"},
        )
        self.assertEqual(status, 415)
        self.assertEqual(json.loads(body), {"error": "wav_audio_required"})

        # Corrupt WAV rejected
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=b"NOT_A_WAV_FILE" * 10,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_audio"})

        # Invalid language hint rejected
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={
                "Cookie": cookie,
                "X-Jarvis-CSRF": csrf,
                "Content-Type": "audio/wav",
                "X-Jarvis-Voice-Language": "fr",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_language"})

        # Valid English transcription
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "text": "What is the status?",
            "language": "en",
            "duration_seconds": 0.4,
            "backend": "faster-whisper",
        })

        # Valid Arabic transcription
        self.stt.result_text = "ما هو الوضع الحالي؟"
        self.stt.result_lang = "ar"
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={
                "Cookie": cookie,
                "X-Jarvis-CSRF": csrf,
                "Content-Type": "audio/wav",
                "X-Jarvis-Voice-Language": "ar",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["text"], "ما هو الوضع الحالي؟")
        self.assertEqual(json.loads(body)["language"], "ar")

        # Unavailable model / timeout / generic error redaction
        self.stt.error = VoiceModelUnavailableError("secret C:\\models\\whisper path")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "stt_model_unavailable"})
        self.assertNotIn(b"secret", body)

        self.stt.error = VoiceTimeoutError("timed out")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(body), {"error": "transcription_timeout"})

        self.stt.error = VoiceTranscriptionError("internal stack secret")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "transcription_failed"})
        self.assertNotIn(b"secret", body)

    def test_synthesize_security_validation_and_wav_playback_headers(self) -> None:
        cookie, csrf = self.pair()
        # Reject unknown fields and identity spoofing
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "Hello", "actor_id": "admin"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})

        # Reject invalid language
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "Hello", "language": "de"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_language"})

        # Success English
        status, headers, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "Very good, sir.", "language": "en"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/wav")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Jarvis-Voice-Backend"), "piper")
        self.assertEqual(headers.get("X-Jarvis-Voice-Language"), "en")
        self.assertEqual(body[:4], b"RIFF")

        # Success Arabic
        status, headers, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "بكل سرور يا سيدي.", "language": "ar"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Jarvis-Voice-Language"), "ar")

        # Unavailable TTS model redacts detail
        self.tts.error = VoiceModelUnavailableError("C:\\private\\voice.onnx missing")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "Hello"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"error": "tts_model_unavailable"})
        self.assertNotIn(b"private", body)

        self.tts.error = VoiceSynthesisError("internal secret")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "Hello"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "synthesis_failed"})
        self.assertNotIn(b"secret", body)

        self.tts.error = VoiceTimeoutError("private timeout detail")
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/synthesize",
            body=json.dumps({"text": "مرحبا.", "language": "ar"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(body), {"error": "synthesis_timeout"})
        self.assertNotIn(b"private", body)

    def test_voice_concurrency_and_rate_limit(self) -> None:
        cookie, csrf = self.pair()
        wav = make_wav_bytes(0.3)
        self.stt.block = True
        first_result: list[tuple] = []

        def run_first():
            first_result.append(self.raw_request(
                "POST",
                f"{API_PREFIX}/voice/transcribe",
                body=wav,
                headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
            ))

        worker = threading.Thread(target=run_first)
        worker.start()
        self.assertTrue(self.stt.started.wait(timeout=5))
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "voice_in_progress"})
        self.stt.release.set()
        worker.join(timeout=5)
        self.assertEqual(first_result[0][0], 200)

        self.stt.block = False
        for _ in range(MAX_VOICE_OPS_PER_MINUTE - 1):
            status, _, _ = self.raw_request(
                "POST",
                f"{API_PREFIX}/voice/transcribe",
                body=wav,
                headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
            )
            self.assertEqual(status, 200)
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/voice/transcribe",
            body=wav,
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
        )
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body), {"error": "rate_limited"})

    def test_unconfigured_backends_return_503_without_fallback(self) -> None:
        other = make_server(
            0,
            provider="test",
            model=MODEL,
            generator=FakeGenerator(),
            pairing_code="no-voice-code",
            voice_service=VoiceService(stt=None, tts=None),
        )
        t = threading.Thread(target=other.serve_forever, daemon=True)
        t.start()
        try:
            cookie, csrf = self.pair(server=other, code="no-voice-code")
            status, _, body = self.raw_request(
                "POST",
                f"{API_PREFIX}/voice/transcribe",
                body=make_wav_bytes(0.3),
                headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
                server=other,
            )
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"error": "voice_stt_not_configured"})

            status, _, body = self.raw_request(
                "POST",
                f"{API_PREFIX}/voice/synthesize",
                body=json.dumps({"text": "Hello"}).encode("utf-8"),
                headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
                server=other,
            )
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body), {"error": "voice_tts_not_configured"})
        finally:
            other.shutdown()
            other.server_close()
            t.join(timeout=2)

    def test_voice_mode_chat_applies_jarvis_persona_and_preserves_default_chat(self) -> None:
        cookie, csrf = self.pair()
        # Standard typed chat preserves GENERAL_CHAT_SYSTEM_PROMPT verbatim
        status, _, _ = self.raw_request(
            "POST",
            f"{API_PREFIX}/chat",
            body=json.dumps({"message": "Hello"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.generator.calls[-1][2], GENERAL_CHAT_SYSTEM_PROMPT)

        # Voice-mode chat activates Jarvis butler persona while keeping safety rules
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/chat",
            body=json.dumps({"message": "Good evening Jarvis", "voice_mode": True}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "reply": "At your service, sir.",
            "model": MODEL,
            "provider": "test",
        })
        prompt = self.generator.calls[-1][2]
        self.assertIn("no access to the user's memories", prompt)
        self.assertIn("British-butler", prompt)
        self.assertIn("Never claim to have executed tasks", prompt)

        # Non-boolean voice_mode rejected
        status, _, body = self.raw_request(
            "POST",
            f"{API_PREFIX}/chat",
            body=json.dumps({"message": "Hello", "voice_mode": "yes"}).encode("utf-8"),
            headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid_request"})

    def test_voice_workflow_preserves_workspace_permissions_and_stores_no_audio_or_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jarvis.db"
            db = Database(db_path)
            db.initialize()
            ws = db.create_workspace("Atlas Voice Test", WorkspaceKind.PERSONAL, "alice", "ws-voice")
            db.create_workspace("Bob Private", WorkspaceKind.PERSONAL, "bob", "ws-bob")

            voice_server = make_server(
                0,
                provider="test",
                model=MODEL,
                generator=FakeGenerator("No evidence available."),
                pairing_code="ws-voice-code",
                actor_id="alice",
                database=db,
                voice_service=VoiceService(stt=self.stt, tts=self.tts, persona="jarvis"),
            )
            t = threading.Thread(target=voice_server.serve_forever, daemon=True)
            t.start()
            try:
                cookie, csrf = self.pair(server=voice_server, code="ws-voice-code")
                # Attempting to select Bob's unauthorized workspace fails closed
                status, _, _ = self.raw_request(
                    "PUT",
                    f"{API_PREFIX}/session/workspace",
                    body=json.dumps({"workspace_id": "ws-bob"}).encode("utf-8"),
                    headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
                    server=voice_server,
                )
                self.assertEqual(status, 404)

                # Select Alice's authorized workspace
                status, _, _ = self.raw_request(
                    "PUT",
                    f"{API_PREFIX}/session/workspace",
                    body=json.dumps({"workspace_id": ws.id}).encode("utf-8"),
                    headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
                    server=voice_server,
                )
                self.assertEqual(status, 200)

                # Transcribe unique sensitive utterance
                secret_phrase = "UNIQUE_VOICE_UTTERANCE_NEVER_STORE_9911"
                self.stt.result_text = secret_phrase
                status, _, body = self.raw_request(
                    "POST",
                    f"{API_PREFIX}/voice/transcribe",
                    body=make_wav_bytes(0.35),
                    headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "audio/wav"},
                    server=voice_server,
                )
                self.assertEqual(status, 200)
                recognized = json.loads(body)["text"]
                self.assertEqual(recognized, secret_phrase)

                # Send recognized text to authenticated workspace chat -> abstains safely (no unauthorized data)
                status, _, body = self.raw_request(
                    "POST",
                    f"{API_PREFIX}/chat",
                    body=json.dumps({"message": recognized, "voice_mode": True}).encode("utf-8"),
                    headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
                    server=voice_server,
                )
                self.assertEqual(status, 200)
                chat_payload = json.loads(body)
                self.assertEqual(chat_payload["workspace_id"], ws.id)
                self.assertTrue(chat_payload["abstained"])

                # Synthesize reply
                status, _, wav_out = self.raw_request(
                    "POST",
                    f"{API_PREFIX}/voice/synthesize",
                    body=json.dumps({"text": chat_payload["reply"], "language": "en"}).encode("utf-8"),
                    headers={"Cookie": cookie, "X-Jarvis-CSRF": csrf, "Content-Type": "application/json"},
                    server=voice_server,
                )
                self.assertEqual(status, 200)
                self.assertEqual(wav_out[:4], b"RIFF")

                # Verify zero audio files written in tmpdir and zero transcripts persisted in SQLite
                tmp_files = sorted(p.name for p in Path(tmp).iterdir())
                self.assertEqual([name for name in tmp_files if name.endswith(".wav")], [])
                raw_db_bytes = db_path.read_bytes()
                self.assertNotIn(secret_phrase.encode("utf-8"), raw_db_bytes)
            finally:
                voice_server.shutdown()
                voice_server.server_close()
                t.join(timeout=2)

    def test_frontend_voice_assets_enforce_local_only_privacy_and_no_browser_speech_fallback(self) -> None:
        root = Path(__file__).parents[1] / "prototypes/m03_b1"
        app_js = (root / "app.js").read_text(encoding="utf-8")
        voice_mjs = (root / "voice-ui-state.mjs").read_text(encoding="utf-8")
        index_html = (root / "index.html").read_text(encoding="utf-8")

        # Never silently fall back to browser speech synthesis or recognition
        for text in (app_js, voice_mjs, index_html):
            self.assertNotIn("speechSynthesis", text)
            self.assertNotIn("SpeechSynthesisUtterance", text)
            self.assertNotIn("SpeechRecognition", text)
            self.assertNotIn("webkitSpeechRecognition", text)

        # Static file served by local service
        status, headers, body = self.raw_request("GET", "/voice-ui-state.mjs")
        self.assertEqual(status, 200)
        self.assertIn(b"encodePcm16Wav", body)
        self.assertIn("media-src 'self' blob:", headers.get("Content-Security-Policy", ""))

        # Required endpoints, cleanup hooks, and controls present
        self.assertIn('fetch("/api/v1/voice/status"', app_js)
        self.assertIn('fetch("/api/v1/voice/transcribe"', app_js)
        self.assertIn('fetch("/api/v1/voice/synthesize"', app_js)
        self.assertIn("navigator.mediaDevices.getUserMedia", app_js)
        self.assertIn("if (voiceMicAcquisitionPending) return;", app_js)
        self.assertIn("voiceMicAcquisitionPending = true;", app_js)
        self.assertIn("sessionToken !== csrfToken", app_js)
        self.assertIn("!isVoiceOperationCurrent(voiceGeneration, sessionToken, workspaceId)", app_js)
        self.assertIn("if (fromVoice) voiceChatInFlight = true;", app_js)
        self.assertIn("if (fromVoice && !isVoiceOperationCurrent", app_js)
        self.assertIn("userEntry.remove();", app_js)
        self.assertIn("track.stop()", app_js)
        self.assertIn("URL.revokeObjectURL", app_js)
        self.assertIn('window.addEventListener("pagehide"', app_js)
        self.assertIn('window.addEventListener("beforeunload"', app_js)
        self.assertIn('id="voice-ptt"', index_html)
        self.assertIn('id="voice-play"', index_html)
        self.assertIn('id="voice-stop"', index_html)
        self.assertIn('id="voice-mute"', index_html)
        self.assertIn('id="voice-transcript-toggle"', index_html)


if __name__ == "__main__":
    unittest.main()

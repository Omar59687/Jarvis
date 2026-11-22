import test from "node:test";
import assert from "node:assert/strict";
import {
  VOICE_STATES,
  applyVoiceStatus,
  canStartPushToTalk,
  createVoiceState,
  detectReplyLanguage,
  encodePcm16Wav,
  interruptVoiceState,
  resetVoiceStateForSessionEnd,
  toggleVoiceMute,
  toggleVoiceTranscript,
  transitionVoiceState,
  voiceErrorMessage,
  voiceStateLabel,
} from "../prototypes/m03_b1/voice-ui-state.mjs";

test("voice state machine covers full push-to-talk lifecycle and required states", () => {
  assert.deepEqual(VOICE_STATES, [
    "stopped",
    "requesting_permission",
    "listening",
    "transcribing",
    "thinking",
    "speaking",
    "error",
  ]);
  let state = createVoiceState();
  assert.equal(state.status, "stopped");
  assert.equal(state.mode, "push_to_talk");
  assert.equal(canStartPushToTalk(state, { authenticated: true, sending: false, workspaceServiceUnavailable: false }), false);

  state = applyVoiceStatus(state, {
    mode: "push_to_talk",
    stt_configured: true,
    stt_ready: true,
    stt_backend: "faster-whisper",
    tts_configured: true,
    tts_ready: true,
    tts_backend: "piper",
    tts_languages: ["ar", "en"],
    persona: "jarvis",
    max_audio_bytes: 2097152,
    max_audio_seconds: 30,
  });
  assert.equal(canStartPushToTalk(state, { authenticated: false, sending: false, workspaceServiceUnavailable: false }), false);
  assert.equal(canStartPushToTalk(state, { authenticated: true, sending: false, workspaceServiceUnavailable: false }), true);

  state = transitionVoiceState(state, "listening");
  assert.equal(state.status, "listening");
  state = transitionVoiceState(state, "transcribing");
  assert.equal(state.status, "transcribing");
  state = transitionVoiceState(state, "thinking", { transcript: "Hello Jarvis", replyLanguage: "en" });
  assert.equal(state.status, "thinking");
  assert.equal(state.lastTranscript, "Hello Jarvis");
  state = transitionVoiceState(state, "speaking", { replyText: "Good evening, sir.", hasReplayAudio: true });
  assert.equal(state.status, "speaking");
  assert.equal(state.hasReplayAudio, true);
  // Interruptible while speaking (user can press push-to-talk again)
  assert.equal(canStartPushToTalk(state, { authenticated: true, sending: false, workspaceServiceUnavailable: false }), true);

  state = transitionVoiceState(state, "stopped");
  assert.equal(state.status, "stopped");
  assert.equal(state.errorCode, null);
});

test("interrupt and session teardown invalidate stale async generations and clear sensitive state", () => {
  let state = createVoiceState();
  state = applyVoiceStatus(state, { stt_configured: true, stt_ready: true, tts_configured: true, tts_ready: true });
  state = transitionVoiceState(state, "speaking", {
    transcript: "Private utterance",
    replyText: "Private reply",
    hasReplayAudio: true,
  });
  const interrupted = interruptVoiceState(state, { clearReply: false });
  assert.equal(interrupted.status, "stopped");
  assert.equal(interrupted.generation, state.generation + 1);
  assert.equal(interrupted.lastReplyText, "Private reply");

  const cleared = interruptVoiceState(interrupted, { clearReply: true });
  assert.equal(cleared.lastTranscript, "");
  assert.equal(cleared.lastReplyText, "");
  assert.equal(cleared.hasReplayAudio, false);

  const muted = toggleVoiceMute(cleared);
  assert.equal(muted.muted, true);
  const hiddenTranscript = toggleVoiceTranscript(muted);
  assert.equal(hiddenTranscript.showTranscript, false);

  const afterLogout = resetVoiceStateForSessionEnd(hiddenTranscript);
  assert.equal(afterLogout.status, "stopped");
  assert.equal(afterLogout.sttConfigured, false);
  assert.equal(afterLogout.ttsConfigured, false);
  assert.equal(afterLogout.lastTranscript, "");
  assert.equal(afterLogout.lastReplyText, "");
  assert.equal(afterLogout.generation, hiddenTranscript.generation + 1);
  // UI preferences preserved across reset
  assert.equal(afterLogout.muted, true);
  assert.equal(afterLogout.showTranscript, false);
});

test("localized EN/AR voice states, errors, and language detection", () => {
  for (const status of VOICE_STATES) {
    assert.ok(voiceStateLabel(status, "en").length > 0);
    assert.ok(voiceStateLabel(status, "ar").length > 0);
  }
  assert.match(voiceErrorMessage("mic_permission_denied", "en"), /Microphone permission/);
  assert.match(voiceErrorMessage("mic_permission_denied", "ar"), /الميكروفون/);
  assert.match(voiceErrorMessage("stt_model_unavailable", "en"), /faster-whisper/);
  assert.match(voiceErrorMessage("tts_model_unavailable", "ar"), /الرد النصي/);
  assert.equal(detectReplyLanguage("Good morning, sir.", "en"), "en");
  assert.equal(detectReplyLanguage("صباح الخير يا سيدي", "en"), "ar");
});

test("encodePcm16Wav downsamples cleanly and enforces duration and size limits", () => {
  // 0.5 seconds at 48 kHz -> 8000 samples at 16 kHz -> 16044 bytes WAV
  const input = new Float32Array(24000);
  for (let i = 0; i < input.length; i += 1) {
    input[i] = 0.25 * Math.sin((2 * Math.PI * 440 * i) / 48000);
  }
  const encoded = encodePcm16Wav([input.subarray(0, 12000), input.subarray(12000)], 48000);
  assert.equal(encoded.sampleRate, 16000);
  assert.ok(Math.abs(encoded.durationSeconds - 0.5) < 0.01);
  assert.equal(encoded.wavBytes.length, 44 + 8000 * 2);
  // RIFF / WAVE header check
  assert.equal(String.fromCharCode(...encoded.wavBytes.slice(0, 4)), "RIFF");
  assert.equal(String.fromCharCode(...encoded.wavBytes.slice(8, 12)), "WAVE");

  // Too short rejected
  assert.throws(
    () => encodePcm16Wav([new Float32Array(100)], 16000),
    (err) => err.code === "audio_too_short",
  );
  // Too long rejected
  assert.throws(
    () => encodePcm16Wav([new Float32Array(32000)], 16000, { maxSeconds: 1 }),
    (err) => err.code === "audio_too_long",
  );
  // Too large rejected
  assert.throws(
    () => encodePcm16Wav([new Float32Array(16000)], 16000, { maxBytes: 1000 }),
    (err) => err.code === "audio_too_large",
  );
});

test("pending microphone permission can be cancelled and invalidates the acquisition generation", () => {
  let state = createVoiceState();
  state = transitionVoiceState(state, "requesting_permission");
  assert.equal(voiceStateLabel(state.status, "en"), "Waiting for microphone permission…");
  assert.equal(voiceStateLabel(state.status, "ar"), "بانتظار إذن الميكروفون…");
  assert.equal(canStartPushToTalk(state, { authenticated: true, sending: false, workspaceServiceUnavailable: false }), false);
  const cancelled = interruptVoiceState(state);
  assert.equal(cancelled.status, "stopped");
  assert.equal(cancelled.generation, state.generation + 1);
});

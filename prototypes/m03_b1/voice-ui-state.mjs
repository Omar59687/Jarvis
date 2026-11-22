export const VOICE_STATES = Object.freeze([
  "stopped",
  "requesting_permission",
  "listening",
  "transcribing",
  "thinking",
  "speaking",
  "error",
]);

export const DEFAULT_MAX_AUDIO_BYTES = 2 * 1024 * 1024;
export const DEFAULT_MAX_AUDIO_SECONDS = 30;
export const MIN_AUDIO_SECONDS = 0.1;
export const TARGET_SAMPLE_RATE = 16000;

const ARABIC_CHAR_RE = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/;

const STATE_LABELS = Object.freeze({
  stopped: ["Stopped · Push to talk", "متوقف · اضغط للتحدث"],
  requesting_permission: ["Waiting for microphone permission…", "بانتظار إذن الميكروفون…"],
  listening: ["Listening…", "جارٍ الاستماع…"],
  transcribing: ["Transcribing locally…", "جارٍ التحويل المحلي للنص…"],
  thinking: ["Thinking locally…", "جارٍ التفكير محليًا…"],
  speaking: ["Speaking locally…", "جارٍ النطق محليًا…"],
  error: ["Voice error", "خطأ في الصوت"],
});

const VOICE_ERRORS = Object.freeze({
  mic_permission_denied: [
    "Microphone permission was denied. Allow microphone access in your browser, then press the mic button again.",
    "رُفض إذن الميكروفون. اسمح بالوصول إلى الميكروفون في المتصفح ثم اضغط زر الميكروفون مجددًا.",
  ],
  mic_unavailable: [
    "No microphone capture API or input device is available in this browser.",
    "لا يتوفر ميكروفون أو واجهة تسجيل صوتي في هذا المتصفح.",
  ],
  audio_too_short: [
    "Recording was too short. Hold or keep listening briefly while speaking.",
    "كان التسجيل قصيرًا جدًا. استمر في التحدث للحظة قبل الإيقاف.",
  ],
  audio_too_long: [
    "Recording reached the maximum local duration limit.",
    "وصل التسجيل إلى الحد الأقصى المسموح للمدة المحلية.",
  ],
  audio_too_large: [
    "Audio clip exceeded the maximum local upload size.",
    "تجاوز المقطع الصوتي الحد الأقصى لحجم الرفع المحلي.",
  ],
  invalid_audio: [
    "Captured audio could not be validated as uncompressed local WAV.",
    "تعذر التحقق من الصوت المسجل كملف WAV محلي غير مضغوط.",
  ],
  voice_stt_not_configured: [
    "Local speech-to-text is not configured on this Jarvis server.",
    "التحويل المحلي للكلام إلى نص غير مهيأ على خادم جارفس هذا.",
  ],
  stt_model_unavailable: [
    "Configured local speech-to-text model is unavailable. Verify the local faster-whisper path.",
    "نموذج التحويل المحلي للكلام إلى نص غير متاح. تحقق من مسار faster-whisper المحلي.",
  ],
  transcription_failed: [
    "Local transcription failed. Try speaking again or type your message.",
    "فشل التحويل المحلي إلى نص. حاول التحدث مجددًا أو اكتب رسالتك.",
  ],
  transcription_timeout: [
    "Local speech transcription timed out.",
    "انتهت مهلة التحويل المحلي للكلام إلى نص.",
  ],
  empty_transcript: [
    "No speech was recognized in that recording.",
    "لم يُتعرّف على كلام في هذا التسجيل.",
  ],
  voice_tts_not_configured: [
    "Local speech synthesis is not configured. Text reply remains available.",
    "توليد الصوت المحلي غير مهيأ. يبقى الرد النصي متاحًا.",
  ],
  tts_model_unavailable: [
    "Configured local voice model is unavailable. Text reply remains available.",
    "نموذج الصوت المحلي المهيأ غير متاح. يبقى الرد النصي متاحًا.",
  ],
  arabic_tts_unavailable: [
    "Arabic voice output is temporarily unavailable. Text reply remains available.",
    "الإخراج الصوتي بالعربية غير متاح مؤقتًا. يظل الرد النصي متاحًا.",
  ],
  synthesis_failed: [
    "Local speech synthesis failed. Text reply remains available.",
    "فشل توليد الصوت المحلي. يبقى الرد النصي متاحًا.",
  ],
  synthesis_timeout: [
    "Local speech synthesis timed out. Text reply remains available.",
    "انتهت مهلة توليد الصوت المحلي. يبقى الرد النصي متاحًا.",
  ],
  playback_failed: [
    "Browser audio playback was blocked or failed. Press Play to retry.",
    "تعذر تشغيل الصوت في المتصفح. اضغط تشغيل للمحاولة مجددًا.",
  ],
  voice_in_progress: [
    "Another local voice request is already running.",
    "هناك طلب صوتي محلي آخر قيد التنفيذ بالفعل.",
  ],
  rate_limited: [
    "Voice request rate limit reached. Wait a moment before trying again.",
    "بلغت حد طلبات الصوت. انتظر قليلًا قبل المحاولة مجددًا.",
  ],
  authentication_required: [
    "Pair this browser before using local voice.",
    "اربط هذا المتصفح قبل استخدام الصوت المحلي.",
  ],
  invalid_language: [
    "Unsupported voice language selection.",
    "لغة الصوت المحددة غير مدعومة.",
  ],
  default: [
    "Local voice operation could not be completed.",
    "تعذر إكمال العملية الصوتية المحلية.",
  ],
});

export function createVoiceState() {
  return {
    status: "stopped",
    mode: "push_to_talk",
    muted: false,
    showTranscript: true,
    sttConfigured: false,
    sttReady: false,
    sttBackend: null,
    ttsConfigured: false,
    ttsReady: false,
    ttsBackend: null,
    ttsLanguages: [],
    persona: "default",
    maxAudioBytes: DEFAULT_MAX_AUDIO_BYTES,
    maxAudioSeconds: DEFAULT_MAX_AUDIO_SECONDS,
    lastTranscript: "",
    lastReplyText: "",
    lastReplyLanguage: "en",
    hasReplayAudio: false,
    errorCode: null,
    generation: 0,
  };
}

export function applyVoiceStatus(state, payload) {
  if (!payload || typeof payload !== "object") {
    return {
      ...state,
      sttConfigured: false,
      sttReady: false,
      sttBackend: null,
      ttsConfigured: false,
      ttsReady: false,
      ttsBackend: null,
      ttsLanguages: [],
    };
  }
  const maxBytes = Number.isInteger(payload.max_audio_bytes) && payload.max_audio_bytes >= 1024
    ? payload.max_audio_bytes
    : state.maxAudioBytes;
  const maxSeconds = typeof payload.max_audio_seconds === "number"
    && Number.isFinite(payload.max_audio_seconds) && payload.max_audio_seconds >= 1
    ? payload.max_audio_seconds
    : state.maxAudioSeconds;
  return {
    ...state,
    mode: payload.mode === "push_to_talk" ? "push_to_talk" : "push_to_talk",
    sttConfigured: payload.stt_configured === true,
    sttReady: payload.stt_ready === true,
    sttBackend: typeof payload.stt_backend === "string" ? payload.stt_backend : null,
    ttsConfigured: payload.tts_configured === true,
    ttsReady: payload.tts_ready === true,
    ttsBackend: typeof payload.tts_backend === "string" ? payload.tts_backend : null,
    ttsLanguages: Array.isArray(payload.tts_languages)
      ? payload.tts_languages.filter((item) => typeof item === "string")
      : [],
    persona: typeof payload.persona === "string" ? payload.persona : "default",
    maxAudioBytes: maxBytes,
    maxAudioSeconds: maxSeconds,
  };
}

export function canStartPushToTalk(state, { authenticated, sending, workspaceServiceUnavailable }) {
  if (!authenticated || sending || workspaceServiceUnavailable) return false;
  if (!state.sttConfigured) return false;
  return state.status === "stopped" || state.status === "error"
    || state.status === "speaking" || state.status === "thinking";
}

export function transitionVoiceState(state, nextStatus, options = {}) {
  if (!VOICE_STATES.includes(nextStatus)) {
    throw new Error(`Invalid voice state: ${String(nextStatus)}`);
  }
  const next = {
    ...state,
    status: nextStatus,
    errorCode: nextStatus === "error" ? (options.errorCode ?? "default") : null,
  };
  if (typeof options.transcript === "string") next.lastTranscript = options.transcript;
  if (typeof options.replyText === "string") next.lastReplyText = options.replyText;
  if (options.replyLanguage === "en" || options.replyLanguage === "ar") {
    next.lastReplyLanguage = options.replyLanguage;
  }
  if (typeof options.hasReplayAudio === "boolean") next.hasReplayAudio = options.hasReplayAudio;
  return next;
}

export function interruptVoiceState(state, { clearReply = false } = {}) {
  return {
    ...state,
    status: "stopped",
    errorCode: null,
    generation: state.generation + 1,
    ...(clearReply
      ? { lastTranscript: "", lastReplyText: "", hasReplayAudio: false }
      : {}),
  };
}

export function resetVoiceStateForSessionEnd(state) {
  return {
    ...createVoiceState(),
    muted: state.muted,
    showTranscript: state.showTranscript,
    generation: state.generation + 1,
  };
}

export function toggleVoiceMute(state) {
  return {
    ...state,
    muted: !state.muted,
  };
}

export function toggleVoiceTranscript(state) {
  return {
    ...state,
    showTranscript: !state.showTranscript,
  };
}

export function voiceStateLabel(status, language = "en") {
  const pair = STATE_LABELS[status] ?? STATE_LABELS.stopped;
  return pair[language === "ar" ? 1 : 0];
}

export function voiceErrorMessage(code, language = "en") {
  const pair = VOICE_ERRORS[code] ?? VOICE_ERRORS.default;
  return pair[language === "ar" ? 1 : 0];
}

export function detectReplyLanguage(text, fallbackLanguage = "en") {
  if (typeof text === "string" && ARABIC_CHAR_RE.test(text)) return "ar";
  return fallbackLanguage === "ar" ? "ar" : "en";
}

export function voiceLanguageUnavailableError(state, language) {
  if (language === "ar" && !state?.ttsLanguages?.includes("ar")) {
    return "arabic_tts_unavailable";
  }
  return null;
}

export function encodePcm16Wav(
  chunks,
  inputSampleRate,
  {
    targetSampleRate = TARGET_SAMPLE_RATE,
    maxBytes = DEFAULT_MAX_AUDIO_BYTES,
    minSeconds = MIN_AUDIO_SECONDS,
    maxSeconds = DEFAULT_MAX_AUDIO_SECONDS,
  } = {},
) {
  if (!Array.isArray(chunks) || chunks.length === 0) {
    throw Object.assign(new Error("audio_too_short"), { code: "audio_too_short" });
  }
  if (!Number.isFinite(inputSampleRate) || inputSampleRate < 8000 || inputSampleRate > 192000) {
    throw Object.assign(new Error("invalid_audio"), { code: "invalid_audio" });
  }
  let totalInputSamples = 0;
  for (const chunk of chunks) {
    if (!(chunk instanceof Float32Array)) {
      throw Object.assign(new Error("invalid_audio"), { code: "invalid_audio" });
    }
    totalInputSamples += chunk.length;
  }
  if (totalInputSamples === 0) {
    throw Object.assign(new Error("audio_too_short"), { code: "audio_too_short" });
  }

  const outRate = Math.min(Math.max(8000, Math.round(targetSampleRate)), 48000);
  const estimatedOutputSamples = Math.ceil(totalInputSamples * outRate / inputSampleRate);
  if (estimatedOutputSamples / outRate > maxSeconds) {
    throw Object.assign(new Error("audio_too_long"), { code: "audio_too_long" });
  }
  if (44 + estimatedOutputSamples * 2 > maxBytes) {
    throw Object.assign(new Error("audio_too_large"), { code: "audio_too_large" });
  }

  const merged = new Float32Array(totalInputSamples);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }

  let resampled;
  if (Math.round(inputSampleRate) === outRate) {
    resampled = merged;
  } else {
    const ratio = inputSampleRate / outRate;
    const outLength = Math.max(1, Math.floor(merged.length / ratio));
    resampled = new Float32Array(outLength);
    for (let i = 0; i < outLength; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(merged.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
      let sum = 0;
      for (let j = start; j < end; j += 1) {
        sum += merged[j];
      }
      resampled[i] = sum / (end - start);
    }
  }

  const durationSeconds = resampled.length / outRate;
  if (durationSeconds < minSeconds) {
    throw Object.assign(new Error("audio_too_short"), { code: "audio_too_short" });
  }
  if (durationSeconds > maxSeconds) {
    throw Object.assign(new Error("audio_too_long"), { code: "audio_too_long" });
  }

  const dataByteLength = resampled.length * 2;
  const totalBytes = 44 + dataByteLength;
  if (totalBytes > maxBytes) {
    throw Object.assign(new Error("audio_too_large"), { code: "audio_too_large" });
  }

  const buffer = new ArrayBuffer(totalBytes);
  const view = new DataView(buffer);
  const writeAscii = (pos, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(pos + i, text.charCodeAt(i));
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataByteLength, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, outRate, true);
  view.setUint32(28, outRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, dataByteLength, true);

  for (let i = 0; i < resampled.length; i += 1) {
    const sample = Number.isFinite(resampled[i]) ? Math.max(-1, Math.min(1, resampled[i])) : 0;
    const int16 = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
    view.setInt16(44 + i * 2, int16, true);
  }

  return {
    wavBytes: new Uint8Array(buffer),
    durationSeconds,
    sampleRate: outRate,
    byteLength: totalBytes,
  };
}

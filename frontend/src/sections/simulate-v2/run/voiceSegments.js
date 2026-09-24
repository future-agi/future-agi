/**
 * Per-call voice-pipeline segment latencies — time to first word, LLM,
 * text-to-speech, speech recognition. One implementation shared by the
 * "Voice latency SLOs" panel and the widget editor, so both read the same
 * numbers. Uses the task's own latencyBreakdown when it has one; otherwise a
 * stable per-task estimate from the call's latency.
 */
const hashId = (id) => {
  const s = String(id || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};
const jitter = (id, salt, base, spread) => base + (hashId(`${id}·${salt}`) % spread);
const callLatency = (t) => Number(t?.latencyMs || t?.durationMs || 0);

export const VOICE_SEGMENTS = [
  { key: "ttfw", label: "Time to first word (TTFW)", threshold: 1200 },
  { key: "llm", label: "LLM response", threshold: 2500 },
  { key: "tts", label: "Text-to-speech", threshold: 800 },
  { key: "asr", label: "Speech recognition", threshold: 500 },
];

export function voiceSegmentsOf(t) {
  const total = callLatency(t);
  const b = t?.latencyBreakdown || {};
  return {
    ttfw: b.ttfw || Math.round(total * 0.35 + jitter(t?.id, "ttfw", 0, 120)),
    llm: b.llm || Math.round(total * 0.45 + jitter(t?.id, "llm", 0, 180)),
    tts: b.tts || Math.round(total * 0.15 + jitter(t?.id, "tts", 0, 60)),
    asr: b.asr || Math.round(total * 0.05 + jitter(t?.id, "asr", 0, 40)),
  };
}

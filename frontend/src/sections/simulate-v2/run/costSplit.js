/**
 * A call's cost split across the voice pipeline (LLM / TTS / STT / transport).
 * One implementation shared by the "Cost breakdown by pipeline stage" chart
 * and the widget editor's per-stage cost metrics, so both show the same
 * numbers. The prototype's tasks carry only a total cost, so the split is a
 * stable per-task estimate around a typical 55 / 25 / 15 / 5 mix.
 */
const hashId = (id) => {
  const s = String(id || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
};

export const COST_STAGES = [
  { key: "llm", label: "LLM", color: "#7857FC" },
  { key: "tts", label: "TTS", color: "#0EA5E9" },
  { key: "stt", label: "STT", color: "#F59E0B" },
  { key: "transport", label: "Transport", color: "#94A3B8" },
];

export function costSplitOf(t) {
  const c = t?.cost || 0;
  const jitter = ((hashId(t?.id || "") % 100) - 50) / 1000; // ±5%
  const llmShare = Math.max(0.35, Math.min(0.7, 0.55 + jitter));
  const ttsShare = Math.max(0.15, Math.min(0.35, 0.25 - jitter / 2));
  const sttShare = Math.max(0.08, Math.min(0.25, 0.15 + jitter / 3));
  const transShare = Math.max(0.02, 1 - llmShare - ttsShare - sttShare);
  return {
    llm: Number((c * llmShare).toFixed(4)),
    tts: Number((c * ttsShare).toFixed(4)),
    stt: Number((c * sttShare).toFixed(4)),
    transport: Number((c * transShare).toFixed(4)),
  };
}

// The runs a TTS / STT / Image form creates (options-truth L3 G7 W5), in
// the order the create payload lists them (utils.promptConfigTransform):
// STT and Image make one run per model; TTS one per model and picked voice
// (a TTS model with no voice sends nothing). Names follow the created
// runs: `TTS × tts-1-hd · nova`, `STT × whisper-1`, `IMAGE × gpt-image-1`.
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

const voiceOf = (v) => (typeof v === "string" ? v : v?.voice ?? null);

/**
 * @param {string} experimentType  "tts" | "stt" | "image" (else [])
 * @param {object[]} promptConfig  the form's promptConfig
 * @returns {{letter: string, name: string, model: string, voice: string|null}[]}
 */
export function mediaRunsOf(experimentType, promptConfig = []) {
  const type = String(experimentType ?? "").toLowerCase();
  if (!["tts", "stt", "image"].includes(type)) return [];
  const out = [];
  (promptConfig ?? []).forEach((item) => {
    (item?.model ?? [])
      .filter((m) => m?.value)
      .forEach((m) => {
        const voices =
          type === "tts"
            ? (Array.isArray(m.voices) ? m.voices : [])
                .map(voiceOf)
                .filter(Boolean)
            : [null];
        voices.forEach((voice) =>
          out.push({ model: m.value, voice: voice ?? null }),
        );
      });
  });
  return out.map((r, i) => ({
    ...r,
    letter: LETTERS[i] ?? String(i + 1),
    name: `${type.toUpperCase()} × ${r.model}${r.voice ? ` · ${r.voice}` : ""}`,
  }));
}

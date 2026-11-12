// options-truth L7b G3 (typed:model-params): Rerun with changes of a TTS /
// STT / image run offers what that API takes (sources/media.js
// mediaModelParameters offers no LLM sampling param for these types): the
// model, plus the voice for TTS. Models are the priced ones of that type
// (declaredUses UNIT_PRICES per unit, MODEL_PRICES for the token-priced
// speech and image models). Pure.
import { MODEL_PRICES, UNIT_PRICES } from "./declaredUses";

export const MEDIA_TYPES = ["tts", "stt", "image"];

const UNIT_OF = { tts: "1k_chars", stt: "minute", image: "image" };
const TOKEN_PRICED = {
  tts: /-tts$/,
  stt: /-transcribe$/,
  image: /-image(-\d+)?$/,
};

/** The media type of a run config, or null. */
export const mediaTypeOfConfig = (config = {}) =>
  MEDIA_TYPES.includes(config?.media?.type) ? config.media.type : null;

/** The models a media type can rerun on, the run's own first (pure). */
export function mediaModelsOf(type, current = null) {
  if (!UNIT_OF[type]) return [];
  const unit = Object.keys(UNIT_PRICES).filter(
    (m) => UNIT_PRICES[m].per === UNIT_OF[type],
  );
  const token = Object.keys(MODEL_PRICES).filter((m) =>
    TOKEN_PRICED[type].test(m),
  );
  return [...new Set([current, ...unit, ...token].filter(Boolean))];
}

/** The changes a media draft makes (pure): model, and voice for TTS. */
export function mediaChangesOf(config = {}, draft = {}) {
  const type = mediaTypeOfConfig(config);
  if (!type) return [];
  const out = [];
  const m = config.media ?? {};
  if (draft.model && draft.model !== m.model)
    out.push({ axis: "model", field: "id", value: draft.model });
  const voice = String(draft.voice ?? "").trim();
  if (type === "tts" && voice && voice !== (m.voice ?? ""))
    out.push({ axis: "voice", field: "voice", value: voice });
  return out;
}

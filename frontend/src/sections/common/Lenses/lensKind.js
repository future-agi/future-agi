// Which modality lens a set of values needs (R4-SPEC D15; lane F; frozen).
// `call` beats media beats `json` beats text; null = text (R3 views stay).
// Among media the most common kind wins (ties: image, document, audio,
// video, file). Pure.
import {
  callOf,
  conversationOf,
  mediaRefOf,
} from "src/sections/common/Media/mediaRef";

export const LENS_KINDS = Object.freeze([
  "call",
  "image",
  "document",
  "json",
  "audio",
  "video",
]);
const MEDIA_ORDER = ["image", "document", "audio", "video", "file"];

const isJsonish = (v) => {
  if (v && typeof v === "object") return !conversationOf(v);
  if (typeof v !== "string") return false;
  const t = v.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return false;
  try {
    const parsed = JSON.parse(t);
    return (
      Boolean(parsed) && typeof parsed === "object" && !conversationOf(parsed)
    );
  } catch {
    return false;
  }
};

/**
 * @param {Array<*>} values  the lanes' values for one row (any shape)
 * @param {{columnType?: string}} [opts]
 * @returns {"call"|"image"|"document"|"audio"|"video"|"json"|null}
 */
export function lensKindOf(values, opts = {}) {
  const list = (values ?? []).filter((v) => v != null && v !== "");
  if (!list.length) return null;
  if (list.some((v) => callOf(v))) return "call";
  const counts = {};
  list.forEach((v) => {
    const r = mediaRefOf(v, opts);
    const refs = Array.isArray(r) ? r : r ? [r] : [];
    refs.forEach((x) => {
      counts[x.kind] = (counts[x.kind] ?? 0) + 1;
    });
  });
  const media = MEDIA_ORDER.filter((k) => counts[k]).sort(
    (a, b) =>
      counts[b] - counts[a] || MEDIA_ORDER.indexOf(a) - MEDIA_ORDER.indexOf(b),
  );
  if (media.length) return media[0] === "file" ? "document" : media[0];
  if (list.some(isJsonish)) return "json";
  return null;
}

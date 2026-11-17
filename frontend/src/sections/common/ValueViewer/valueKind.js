// How a cell value should render (PRODUCT-SPEC §3.4 "Renderers come from
// ValueViewer"). Pure; L1 extends it.

const MEDIA_TYPES = new Set(["image", "images", "audio", "document", "file"]);
const IMAGE_URL = /^https?:\/\/\S+\.(png|jpe?g|gif|webp|svg)(\?\S*)?$/i;
const AUDIO_URL = /^https?:\/\/\S+\.(mp3|wav|ogg|m4a|webm)(\?\S*)?$/i;

/** Parsed JSON when `text` is a JSON object/array string, else undefined. */
export function parseJsonString(text) {
  if (typeof text !== "string") return undefined;
  const t = text.trim();
  if (
    !(t.startsWith("{") && t.endsWith("}")) &&
    !(t.startsWith("[") && t.endsWith("]"))
  )
    return undefined;
  try {
    const v = JSON.parse(t);
    return v && typeof v === "object" ? v : undefined;
  } catch {
    return undefined;
  }
}

/** `[{role, content}, …]`: a chat transcript. */
export function isChatArray(v) {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every(
      (m) =>
        m &&
        typeof m === "object" &&
        typeof m.role === "string" &&
        ("content" in m || "tool_calls" in m),
    )
  );
}

/**
 * @returns {"empty"|"text"|"json"|"json_string"|"chat"|"image"|"audio"|
 *   "media"|"number"|"boolean"}
 */
export function detectValueKind(value, dataType) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length))
    return "empty";
  if (typeof value === "boolean" || dataType === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (typeof value === "string") {
    if (dataType === "image" || IMAGE_URL.test(value.trim())) return "image";
    if (dataType === "audio" || AUDIO_URL.test(value.trim())) return "audio";
    if (MEDIA_TYPES.has(dataType)) return "media";
    const parsed = parseJsonString(value);
    if (parsed !== undefined) {
      if (Array.isArray(parsed) ? !parsed.length : !Object.keys(parsed).length)
        return "empty";
      return isChatArray(parsed) ? "chat" : "json_string";
    }
    return "text";
  }
  if (isChatArray(value)) return "chat";
  if (typeof value === "object")
    return Object.keys(value).length ? "json" : "empty";
  return "text";
}

/** The value as an object for JSON/chat kinds. */
export function asObject(value) {
  return typeof value === "string" ? parseJsonString(value) : value;
}

/** Exact text for the raw view. */
export function rawText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Clamp long text: `{text, clamped, length}`. */
export function clampText(text, max = 1200) {
  const s = String(text ?? "");
  if (!max || s.length <= max)
    return { text: s, clamped: false, length: s.length };
  return {
    text: `${s.slice(0, max).trimEnd()}…`,
    clamped: true,
    length: s.length,
  };
}

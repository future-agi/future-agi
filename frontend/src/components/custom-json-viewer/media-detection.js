import { isAudioUrlString } from "src/components/inline-audio/audio-detection";

// ── Media detection for JSON viewer values (pure, no React) ──────────
// Class on every media preview control, so the dataset grid can tell a
// preview click apart from a click that should open the row drawer.
export const MEDIA_PREVIEW_CLASS = "json-media-preview";

const HTTP_URL_PREFIX = /^https?:\/\//i;
// Same image extensions the annotation content panel detects.
const IMAGE_EXTENSION = /\.(png|jpg|jpeg|gif|webp|svg)$/;

// Checks only the prefix, so a large base64 value isn't copied.
const hasDataPrefix = (value, kind) => {
  const prefix = `data:${kind}/`;
  return value.slice(0, prefix.length).toLowerCase() === prefix;
};

const isHttpUrl = (value) => {
  if (!HTTP_URL_PREFIX.test(value)) return false;
  try {
    const { protocol } = new URL(value);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
};

// The only values allowed into an <img> or <audio> src: an http(s) URL or a
// data URI of the same media kind. Anything else (script URLs, blob:, file:,
// relative paths, other data types) returns null.
export function getSafeMediaSrc(value, kind) {
  if (typeof value !== "string") return null;
  return hasDataPrefix(value, kind) || isHttpUrl(value) ? value : null;
}

// "image", "audio" or null. Matches data URIs and http(s) URLs that end in a
// known file extension. Never throws.
export function getMediaKind(value) {
  if (typeof value !== "string") return null;
  if (hasDataPrefix(value, "image")) return "image";
  if (hasDataPrefix(value, "audio")) return "audio";
  if (!isHttpUrl(value)) return null;
  if (IMAGE_EXTENSION.test(value.toLowerCase().split("?")[0])) return "image";
  if (isAudioUrlString(value)) return "audio";
  return null;
}

/**
 * Shared media detection and src sanitization helpers for JSON viewers.
 * Detection patterns adapted from content-panel.jsx (lines 756-770)
 * and audio-detection.js.
 */

// Whitelist of allowed URL schemes for media src attributes.
// Rejects javascript:, data:text/html, and other dangerous protocols
// that could lead to XSS when rendered in <img>/<audio>/<source> tags.
const SAFE_DATA_PREFIXES = [
  "data:image/",
  "data:audio/",
];

const SAFE_PROTOCOLS = new Set(["https:", "http:"]);

/**
 * Sanitize a media src value before passing it to an <img>, <audio>,
 * or <source> tag. Only URLs with safe schemes are allowed through;
 * everything else returns an empty string.
 */
export function sanitizeSrc(url) {
  if (typeof url !== "string" || !url) return "";

  // Allow data URIs for recognised media MIME types
  for (const prefix of SAFE_DATA_PREFIXES) {
    if (url.startsWith(prefix)) return url;
  }

  // Allow blob URLs (e.g. object URLs created via URL.createObjectURL)
  if (url.startsWith("blob:")) return url;

  // Allow HTTP(S) — validate by parsing as a URL to reject malformed values
  try {
    const parsed = new URL(url, window.location.origin);
    if (SAFE_PROTOCOLS.has(parsed.protocol)) return url;
  } catch {
    // Not a parseable URL — reject
  }

  return "";
}

/**
 * Returns true when `value` looks like an image — either a data:image
 * base64 string or a URL whose path ends with a common image extension.
 */
export function isImageValue(value) {
  if (typeof value !== "string") return false;
  return (
    value.startsWith("data:image") ||
    /\.(png|jpg|jpeg|gif|webp|svg)(\?|$)/i.test(value)
  );
}

/**
 * Returns true when `value` looks like audio — either a data:audio
 * base64 string or a URL whose path ends with a common audio extension.
 */
export function isAudioValue(value) {
  if (typeof value !== "string") return false;
  return (
    value.startsWith("data:audio") ||
    /\.(mp3|wav|ogg|m4a|aac|flac|webm)(\?|$)/i.test(value)
  );
}

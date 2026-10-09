// Plain-text formatting for custom metadata cells (R17-R24).
//
// Output is always a JS string placed as a React text child. No HTML, Markdown,
// link detection or regex evaluation happens here or in the cell.

export const PREVIEW_LIMIT = 80;
export const TOOLTIP_LIMIT = 2000;
export const EMPTY_TEXT = "-";
const TRUNCATED_SUFFIX = " [truncated]";
const ELLIPSIS = "…";

// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001F\u007F\u2028\u2029]+/g;

function stringify(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "bigint") return value.toString();
  try {
    const json = JSON.stringify(value);
    return typeof json === "string" ? json : "[unserializable]";
  } catch {
    return "[unserializable]";
  }
}

/**
 * @returns {{ text: string, full: string|null, truncated: boolean }}
 *   `text` is the single-line cell preview, `full` the tooltip body (null when
 *   the value is empty) and `truncated` whether either limit applied.
 */
export function formatMetadataValue(value) {
  if (value === undefined || value === null || value === "") {
    return { text: EMPTY_TEXT, full: null, truncated: false };
  }

  const collapsed = stringify(value).replace(CONTROL_RE, " ").trim();
  if (collapsed === "") {
    return { text: EMPTY_TEXT, full: null, truncated: false };
  }

  let text = collapsed;
  let full = collapsed;
  let truncated = false;

  if (collapsed.length > PREVIEW_LIMIT) {
    text = `${collapsed.slice(0, PREVIEW_LIMIT - 1)}${ELLIPSIS}`;
    truncated = true;
  }
  if (collapsed.length > TOOLTIP_LIMIT) {
    full = `${collapsed.slice(0, TOOLTIP_LIMIT)}${TRUNCATED_SUFFIX}`;
    truncated = true;
  }

  return { text, full, truncated };
}

/**
 * Read exactly `metadata[name]` as an own property of a plain object. No
 * prototype walk, no nested-path interpretation (R9/AC9).
 */
export function readMetadataValue(metadata, name) {
  if (Object.prototype.toString.call(metadata) !== "[object Object]") {
    return undefined;
  }
  return Object.prototype.hasOwnProperty.call(metadata, name)
    ? metadata[name]
    : undefined;
}

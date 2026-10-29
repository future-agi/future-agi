// Plain-text formatting for custom metadata cells.
//
// Output is always a JS string placed as a React text child. No HTML, Markdown,
// link detection or regex evaluation happens here or in the cell.

// CSS usually clips first, but does not shorten DOM text. This separate limit
// bounds every cell's text while preserving ordinary grapheme boundaries.
export const PREVIEW_LIMIT = 80;
export const TOOLTIP_LIMIT = 2000;
// Keep pathological single grapheme clusters from bypassing the display
// limits. Normal values still use grapheme limits; this is only a UTF-16
// safety ceiling for values such as an unbounded combining-mark run.
export const PREVIEW_UTF16_LIMIT = 512;
export const MAX_VALUE_UTF16_LENGTH = 4096;
export const EMPTY_TEXT = "-";
// Leads the tooltip, so a scrolled tooltip still shows it.
const TRUNCATED_PREFIX = "[truncated] ";
const ELLIPSIS = "…";
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001F\u007F\u2028\u2029]+/g;

const graphemeSegmenter =
  typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
    : null;

// Grapheme clusters keep flags and ZWJ emoji whole; without Intl.Segmenter,
// code points still never split a surrogate pair.
function* graphemeClusters(text) {
  if (!graphemeSegmenter) {
    yield* text;
    return;
  }
  for (const { segment } of graphemeSegmenter.segment(text)) yield segment;
}

function clipToCodePointBoundary(text, limit) {
  if (text.length <= limit) return text;
  let end = limit;
  // Do not leave a dangling high surrogate when the hard ceiling falls in a
  // surrogate pair. Combining marks and ZWJ sequences may be cut here, but
  // only at a complete code point, which keeps the output bounded.
  if (end > 0 && /[\uD800-\uDBFF]/.test(text[end - 1])) end -= 1;
  return text.slice(0, end);
}

// Return the first `limit` clusters, also stopping at `utf16Limit`. The raw
// ceiling is deliberately secondary to the grapheme limit so ordinary emoji,
// flags and ZWJ sequences keep their existing logical boundaries. If the
// first cluster itself is oversized, clip it at a code-point boundary.
function headIfLonger(
  text,
  limit,
  utf16Limit = Number.POSITIVE_INFINITY,
  dropLastWhenLimit = false,
) {
  const head = [];
  let utf16Length = 0;
  for (const cluster of graphemeClusters(text)) {
    if (head.length === limit) {
      return head.slice(0, dropLastWhenLimit ? -1 : undefined).join("");
    }
    if (utf16Length + cluster.length > utf16Limit) {
      if (head.length === 0)
        head.push(clipToCodePointBoundary(cluster, utf16Limit));
      return head.join("");
    }
    head.push(cluster);
    utf16Length += cluster.length;
  }
  return null;
}

function stringify(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
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

  const bounded = clipToCodePointBoundary(collapsed, MAX_VALUE_UTF16_LENGTH);
  const boundedInput = bounded.length !== collapsed.length;

  // Normal limits count grapheme clusters; the hard ceiling may cut an
  // oversized cluster at a complete code-point boundary.
  let text = bounded;
  let full = bounded;
  let truncated = boundedInput;

  const previewHead = headIfLonger(
    bounded,
    PREVIEW_LIMIT,
    PREVIEW_UTF16_LIMIT - ELLIPSIS.length,
    true,
  );
  if (previewHead) {
    text = `${previewHead}${ELLIPSIS}`;
    truncated = true;
  }
  const tooltipHead = headIfLonger(bounded, TOOLTIP_LIMIT);
  if (tooltipHead) {
    full = `${TRUNCATED_PREFIX}${tooltipHead}`;
    truncated = true;
  } else if (boundedInput) {
    full = `${TRUNCATED_PREFIX}${bounded}`;
  }

  return { text, full, truncated };
}

/**
 * Read exactly `metadata[name]` as an own property of a plain object. No
 * prototype walk, no nested-path interpretation.
 */
export function readMetadataValue(metadata, name) {
  if (Object.prototype.toString.call(metadata) !== "[object Object]") {
    return undefined;
  }
  return Object.prototype.hasOwnProperty.call(metadata, name)
    ? metadata[name]
    : undefined;
}

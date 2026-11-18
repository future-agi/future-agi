// Evidence quotes inside an analysed unit's content. The engine reports each
// quote with `start`/`end` character offsets into `content`; when the offsets
// are missing or do not match (content trimmed, re-encoded), the quote is
// found by searching for it instead.

const normalize = (text) => text.replace(/\s+/g, " ").trim().toLowerCase();

// Where `quote` sits in `content`, or null. Exact match first, then
// case-insensitive, then with whitespace runs collapsed.
function locate(content, quote) {
  if (!quote) return null;
  let at = content.indexOf(quote);
  if (at >= 0) return [at, at + quote.length];
  const lower = content.toLowerCase();
  at = lower.indexOf(quote.toLowerCase());
  if (at >= 0) return [at, at + quote.length];
  // Collapse whitespace on both sides, keeping a map back to content offsets.
  const target = normalize(quote);
  if (!target) return null;
  const map = [];
  let flat = "";
  let space = false;
  for (let i = 0; i < content.length; i += 1) {
    const ch = content[i];
    if (/\s/.test(ch)) {
      if (!space && flat) {
        flat += " ";
        map.push(i);
      }
      space = true;
    } else {
      flat += ch.toLowerCase();
      map.push(i);
      space = false;
    }
  }
  at = flat.indexOf(target);
  if (at < 0) return null;
  return [map[at], map[at + target.length - 1] + 1];
}

/**
 * Character ranges to highlight: [{start, end, index}] sorted and merged,
 * where `index` is the first evidence item the range covers.
 */
export function evidenceRanges(content, evidence) {
  const text = typeof content === "string" ? content : "";
  if (!text || !Array.isArray(evidence)) return [];
  const ranges = [];
  evidence.forEach((item, index) => {
    if (!item) return;
    const quote = typeof item.quote === "string" ? item.quote.trim() : "";
    const { start, end } = item;
    const valid =
      Number.isInteger(start) &&
      Number.isInteger(end) &&
      start >= 0 &&
      end > start &&
      end <= text.length &&
      (!quote || normalize(text.slice(start, end)) === normalize(quote));
    const span = valid ? [start, end] : locate(text, quote);
    if (span) ranges.push({ start: span[0], end: span[1], index });
  });
  ranges.sort((a, b) => a.start - b.start || b.end - a.end);
  const merged = [];
  ranges.forEach((range) => {
    const last = merged[merged.length - 1];
    if (last && range.start <= last.end) {
      last.end = Math.max(last.end, range.end);
      last.index = Math.min(last.index, range.index);
    } else merged.push({ ...range });
  });
  return merged;
}

/** Split content into [{text, mark, index}] segments around the ranges. */
export function splitByRanges(content, ranges) {
  const text = typeof content === "string" ? content : "";
  const segments = [];
  let at = 0;
  (ranges || []).forEach((range) => {
    if (range.start > at)
      segments.push({ text: text.slice(at, range.start), mark: false });
    segments.push({
      text: text.slice(range.start, range.end),
      mark: true,
      index: range.index,
    });
    at = range.end;
  });
  if (at < text.length) segments.push({ text: text.slice(at), mark: false });
  return segments;
}

/** Quotes that could not be placed in the content (shown on their own). */
export const unplacedEvidence = (content, evidence) =>
  (Array.isArray(evidence) ? evidence : []).filter(
    (item) => item?.quote && evidenceRanges(content, [item]).length === 0,
  );

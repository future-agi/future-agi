// W5-1 · did-you-mean for unknown field names (W5-SCOPE §5 A0). A name is
// suggested when its full spelling or its last segment is within 2 edits of
// what was typed (Levenshtein, case-insensitive), or when its last segment
// starts with it (`lang` → metadata.language). At most 3: full-name typos
// first, then last-segment typos, then prefixes; ties in catalogue order.
// Pure.

export const MAX_DISTANCE = 2;
export const MAX_SUGGESTIONS = 3;
// Ranking: a typo of the full name first, then a typo of the last segment
// (same distance), then a prefix of the last segment.
const LAST_SEGMENT_PENALTY = 0.5;
const PREFIX_RANK = MAX_DISTANCE + 1;
// Shortest typed text a prefix match is offered for.
const PREFIX_MIN = 3;

/** Levenshtein distance; stops early once it is past `limit`. */
export function levenshtein(a, b, limit = Infinity) {
  const s = String(a);
  const t = String(b);
  if (s === t) return 0;
  if (Math.abs(s.length - t.length) > limit) return limit + 1;
  let prev = Array.from({ length: t.length + 1 }, (_, j) => j);
  for (let i = 1; i <= s.length; i += 1) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= t.length; j += 1) {
      const cost = s[i - 1] === t[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (cur[j] < best) best = cur[j];
    }
    if (best > limit) return limit + 1;
    prev = cur;
  }
  return prev[t.length];
}

const lastSegment = (name) => {
  const i = name.lastIndexOf(".");
  return i < 0 ? name : name.slice(i + 1);
};

/**
 * Names close to `typed`.
 * @param {string} typed
 * @param {string[]} names  catalogue order
 * @returns {string[]} at most 3
 */
export function didYouMean(typed, names, { max = MAX_SUGGESTIONS } = {}) {
  const q = String(typed ?? "").toLowerCase();
  if (!q) return [];
  const qLast = lastSegment(q);
  const scored = [];
  const seen = new Set();
  (names ?? []).forEach((name, index) => {
    if (seen.has(name)) return;
    seen.add(name);
    const full = name.toLowerCase();
    if (full === q) return;
    const last = lastSegment(full);
    const dFull = levenshtein(q, full, MAX_DISTANCE);
    const dLast = levenshtein(qLast, last, MAX_DISTANCE);
    let rank = null;
    if (dFull <= MAX_DISTANCE) rank = dFull;
    else if (dLast <= MAX_DISTANCE) rank = dLast + LAST_SEGMENT_PENALTY;
    else if (qLast.length >= PREFIX_MIN && last.startsWith(qLast))
      rank = PREFIX_RANK;
    if (rank !== null) scored.push({ name, rank, index });
  });
  return scored
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .slice(0, max)
    .map((s) => s.name);
}

/** `metadata.language` · `a or b` · `a, b or c`. */
export function orList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

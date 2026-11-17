// Word / JSON diff segments for TextDiff (PRODUCT-SPEC §4). Uses the `diff`
// package (diffArrays over whole words, diffJson for JSON) and returns lines
// of `{text, status}` segments in the shape GenerateDiffText renders (it
// joins a line's segments with single spaces).
import { diffArrays, diffJson } from "diff";

const toText = (v) => {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
};

const statusOf = (part) =>
  part.added ? "added" : part.removed ? "removed" : undefined;

/**
 * @param {*} from  the old value (expected, baseline output, earlier version)
 * @param {*} to    the new value
 * @param {"words"|"json"} [kind="words"]
 * @returns {Array<Array<{text: string, status?: "added"|"removed"}>>} lines
 */
export function diffLines(from, to, kind = "words") {
  const a = toText(from);
  const b = toText(to);
  const lines = [[]];
  const push = (text, status) => {
    if (!text) return;
    lines[lines.length - 1].push(status ? { text, status } : { text });
  };

  if (kind === "json") {
    diffJson(
      typeof from === "string" ? safeParse(from) : from ?? "",
      typeof to === "string" ? safeParse(to) : to ?? "",
    ).forEach((part) => {
      const st = statusOf(part);
      part.value
        .replace(/\n$/, "")
        .split("\n")
        .forEach((line, i) => {
          if (i > 0 || lines[lines.length - 1].length) lines.push([]);
          push(line, st);
        });
    });
    return lines.filter((l, i) => l.length || i < lines.length - 1);
  }

  // Whole whitespace-separated words are the unit (so "3–5" -> "5–7" reads
  // as one word replaced, not as character fragments); newlines are tokens.
  diffArrays(tokenize(a), tokenize(b)).forEach((part) => {
    const st = statusOf(part);
    part.value.forEach((token) => {
      if (token === "\n") lines.push([]);
      else push(token, st);
    });
  });
  return lines;
}

/** Words and newlines of a text. */
export const tokenize = (text) => text.match(/\n|[^\s]+/g) ?? [];

function safeParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

/** `{added, removed}` word counts, for a summary line. */
export function diffStats(lines) {
  let added = 0;
  let removed = 0;
  lines.flat().forEach((s) => {
    if (s.status === "added") added += 1;
    if (s.status === "removed") removed += 1;
  });
  return { added, removed };
}

/**
 * RV#12: `+3 / −0 chars` — characters added and removed between two values
 * (common prefix/suffix trimmed, so a typo fix reads `+1 / −1`).
 * @returns {{added: number, removed: number}}
 */
export function charDelta(before, after) {
  const a =
    before == null
      ? ""
      : typeof before === "string"
        ? before
        : JSON.stringify(before);
  const b =
    after == null
      ? ""
      : typeof after === "string"
        ? after
        : JSON.stringify(after);
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p += 1;
  let s = 0;
  while (
    s < a.length - p &&
    s < b.length - p &&
    a[a.length - 1 - s] === b[b.length - 1 - s]
  )
    s += 1;
  return { added: b.length - p - s, removed: a.length - p - s };
}

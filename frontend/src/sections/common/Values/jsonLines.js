// Pure helpers behind CodeLines (R3 P2, RV#7 / R2D-20): a value as
// pretty-printed JSON text, split into numbered lines of coloured tokens.
// Pretty-printed JSON never puts a raw newline inside a string, so every
// line tokenises on its own.

const TOKEN =
  /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

/** A JSON-looking string (`{…}` / `[…]`) parsed, else undefined. */
export function parseJsonText(value) {
  if (typeof value !== "string") return undefined;
  const t = value.trim();
  if (!t || !/^[[{]/.test(t)) return undefined;
  try {
    return JSON.parse(t);
  } catch {
    return undefined;
  }
}

/**
 * A field value as 2-space JSON: JSON text is parsed first (so a stored
 * `[{"name":…}]` prints as a tree), plain text becomes a JSON string,
 * missing values print `null`.
 */
export function prettyJson(value) {
  if (value === undefined || value === null) return "null";
  const parsed = parseJsonText(value);
  const v = parsed === undefined ? value : parsed;
  try {
    return JSON.stringify(v, null, 2) ?? "null";
  } catch {
    return JSON.stringify(String(v));
  }
}

/**
 * One line → `[{t, text}]` with t ∈ key | string | number | boolean |
 * null | punct. Concatenating `text` gives the line back.
 */
export function tokenizeJsonLine(line) {
  const out = [];
  let last = 0;
  line.replace(TOKEN, (match, str, colon, bool, nul, num, offset) => {
    if (offset > last) out.push({ t: "punct", text: line.slice(last, offset) });
    if (str && colon) {
      out.push({ t: "key", text: str });
      out.push({ t: "punct", text: colon });
    } else if (str) out.push({ t: "string", text: str });
    else if (bool) out.push({ t: "boolean", text: bool });
    else if (nul) out.push({ t: "null", text: nul });
    else out.push({ t: "number", text: num });
    last = offset + match.length;
    return match;
  });
  if (last < line.length) out.push({ t: "punct", text: line.slice(last) });
  return out;
}

/** Text → `[{n, tokens}]` (1-based); non-JSON text gets one plain token. */
export function toCodeLines(text, { json = true } = {}) {
  return String(text ?? "")
    .split("\n")
    .map((line, i) => ({
      n: i + 1,
      tokens: json ? tokenizeJsonLine(line) : [{ t: "punct", text: line }],
    }));
}

/**
 * Split a token's text into words and the spaces between them, so a wrap
 * can only break at a space (never inside `A-4411`).
 */
export const wordsOf = (text) =>
  String(text ?? "")
    .split(/( +)/)
    .filter(Boolean);

/** `12 lines`, `1 line`. */
export const lineCountText = (n) => `${n} line${n === 1 ? "" : "s"}`;

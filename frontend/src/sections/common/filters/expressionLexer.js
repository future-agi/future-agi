// W5-1 expression language · the lexer (W5-SCOPE §5 A0). One line of text →
// tokens `{type, value, at, length}`. Pure; never throws; no regex over the
// whole input and no eval: one pass, one character at a time.
//
// Token types: num · str · ident · name (back-quoted) · runref (#2B, #2B·2)
// · op (= != > >= < <= + - * / ( ) , .) · end.

export const EXPRESSION_LIMITS = Object.freeze({
  maxLength: 2000,
  maxDepth: 32,
});

export const TOO_LONG = "Expression is too long (2,000 characters at most).";
export const TOO_DEEP = "Expression is nested too deeply (32 levels at most).";

/** Keywords, matched case-insensitively. */
export const KEYWORDS = Object.freeze([
  "or",
  "and",
  "not",
  "in",
  "contains",
  "startswith",
  "is",
  "empty",
  "true",
  "false",
  "null",
]);

/** Verdict words: a pass/fail eval compared with one compares its verdict. */
export const VERDICT_WORDS = Object.freeze([
  "pass",
  "fail",
  "skipped",
  "na",
  "error",
]);

/** Run quantifiers and the baseline, used as `<word>.field`. */
export const RUN_WORDS = Object.freeze(["baseline", "any", "all"]);

const isDigit = (c) => c >= "0" && c <= "9";
const isIdentStart = (c) =>
  (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "_";
const isIdentChar = (c) => isIdentStart(c) || isDigit(c);
const isSpace = (c) => c === " " || c === "\t" || c === "\n" || c === "\r";
const isLetter = (c) => (c >= "a" && c <= "z") || (c >= "A" && c <= "Z");

// The attempt separator of a run label (#9A·14): U+00B7, or a typed "." when
// digits follow a label (handled in the run-ref scan).
const MIDDOT = "·";

const TWO_CHAR_OPS = ["!=", ">=", "<=", "=="];
const ONE_CHAR_OPS = "=<>+-*/(),.";

const err = (message, at, length = 1) => ({ error: { message, at, length } });

/**
 * Tokenize one expression.
 * @param {string} text
 * @returns {{tokens: Array<{type: string, value: *, at: number, length: number}>}
 *   | {error: {message: string, at: number, length: number}}}
 */
export function tokenize(text) {
  const src = String(text ?? "");
  if (src.length > EXPRESSION_LIMITS.maxLength)
    return err(
      TOO_LONG,
      EXPRESSION_LIMITS.maxLength,
      src.length - EXPRESSION_LIMITS.maxLength,
    );
  const tokens = [];
  let i = 0;
  const push = (type, value, at, length) =>
    tokens.push({ type, value, at, length });
  while (i < src.length) {
    const c = src[i];
    if (isSpace(c)) {
      i += 1;
      continue;
    }
    const start = i;
    // Strings: "double" or 'single', backslash escapes the quote / backslash.
    if (c === '"' || c === "'" || c === "“" || c === "‘") {
      const close = c === "“" ? "”" : c === "‘" ? "’" : c;
      let out = "";
      i += 1;
      let closed = false;
      while (i < src.length) {
        const d = src[i];
        if (d === "\\" && i + 1 < src.length) {
          out += src[i + 1];
          i += 2;
          continue;
        }
        if (d === close) {
          closed = true;
          i += 1;
          break;
        }
        out += d;
        i += 1;
      }
      if (!closed)
        return err(`Missing closing quote (${c}).`, start, src.length - start);
      push("str", out, start, i - start);
      continue;
    }
    // Back-quoted names: `Draft reply (gpt-4o-mini)`.
    if (c === "`") {
      const end = src.indexOf("`", i + 1);
      if (end < 0)
        return err(
          "Missing closing back quote (`).",
          start,
          src.length - start,
        );
      const name = src.slice(i + 1, end);
      if (!name.trim())
        return err("Empty back-quoted name.", start, end - start + 1);
      push("name", name, start, end - start + 1);
      i = end + 1;
      continue;
    }
    // Numbers: 12, 0.5, .5, 1e3, 1_000 is not a number.
    // After a name, "." is the path dot (output.items.0), never a number.
    const prev = tokens[tokens.length - 1];
    const afterName =
      prev &&
      (prev.type === "ident" ||
        prev.type === "name" ||
        prev.type === "runref" ||
        (prev.type === "op" && prev.value === ")"));
    if (isDigit(c) || (c === "." && !afterName && isDigit(src[i + 1] ?? ""))) {
      let j = i;
      while (j < src.length && isDigit(src[j])) j += 1;
      if (src[j] === "." && isDigit(src[j + 1] ?? "")) {
        j += 1;
        while (j < src.length && isDigit(src[j])) j += 1;
      }
      if (
        (src[j] === "e" || src[j] === "E") &&
        (isDigit(src[j + 1] ?? "") ||
          ((src[j + 1] === "-" || src[j + 1] === "+") &&
            isDigit(src[j + 2] ?? "")))
      ) {
        j += 2;
        while (j < src.length && isDigit(src[j])) j += 1;
      }
      if (isIdentStart(src[j] ?? "")) {
        let k = j;
        while (k < src.length && isIdentChar(src[k])) k += 1;
        return err(`“${src.slice(i, k)}” is not a number or a name.`, i, k - i);
      }
      push("num", Number(src.slice(i, j)), i, j - i);
      i = j;
      continue;
    }
    // Run refs: #2B, #12AB, #2B·2 (an attempt). Case-insensitive letters.
    if (c === "#") {
      let j = i + 1;
      while (j < src.length && isDigit(src[j])) j += 1;
      const digitsEnd = j;
      while (j < src.length && isLetter(src[j])) j += 1;
      if (digitsEnd === i + 1 || j === digitsEnd)
        return err("A run is written like #2B.", i, Math.max(1, j - i));
      let label = `#${src.slice(i + 1, digitsEnd)}${src.slice(digitsEnd, j).toUpperCase()}`;
      if (src[j] === MIDDOT && isDigit(src[j + 1] ?? "")) {
        let k = j + 1;
        while (k < src.length && isDigit(src[k])) k += 1;
        label += `${MIDDOT}${src.slice(j + 1, k)}`;
        j = k;
      }
      push("runref", label, i, j - i);
      i = j;
      continue;
    }
    if (isIdentStart(c)) {
      let j = i;
      while (j < src.length && isIdentChar(src[j])) j += 1;
      push("ident", src.slice(i, j), i, j - i);
      i = j;
      continue;
    }
    const two = src.slice(i, i + 2);
    if (TWO_CHAR_OPS.includes(two)) {
      push("op", two === "==" ? "=" : two, i, 2);
      i += 2;
      continue;
    }
    if (c === "≠") {
      push("op", "!=", i, 1);
      i += 1;
      continue;
    }
    if (c === "≥" || c === "≤") {
      push("op", c === "≥" ? ">=" : "<=", i, 1);
      i += 1;
      continue;
    }
    if (ONE_CHAR_OPS.includes(c)) {
      push("op", c, i, 1);
      i += 1;
      continue;
    }
    return err(`Unexpected “${c}”.`, i, 1);
  }
  push("end", null, src.length, 0);
  return { tokens };
}

/** Lowercased identifier when it is a keyword, else null. */
export const keywordOf = (tok) =>
  tok?.type === "ident" && KEYWORDS.includes(tok.value.toLowerCase())
    ? tok.value.toLowerCase()
    : null;

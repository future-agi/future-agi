// Validation of a Row view edit (PRODUCT-SPEC §3.4 Editing). Pure.
import { parse as parseYaml, stringify as yamlStringify } from "yaml";
// Returns the value to store in the change set: JSON fields as compact JSON
// strings (how the grid stores them), numbers as numbers, booleans as
// "true" / "false", text unchanged.

const JSON_TYPES = new Set(["json", "array"]);

/**
 * @param {string} draft  the editor text
 * @param {string} [dataType]
 * @returns {{ok: true, value: *} | {ok: false, error: string}}
 */
export function validateEdit(draft, dataType) {
  const text = draft ?? "";
  if (JSON_TYPES.has(dataType)) {
    if (!text.trim()) return { ok: true, value: "" };
    try {
      return { ok: true, value: JSON.stringify(JSON.parse(text)) };
    } catch (e) {
      const msg = String(e?.message ?? e)
        .replace(/^JSON\.parse: /, "")
        .replace(/^Unexpected token/, "Unexpected token");
      return { ok: false, error: `Invalid JSON: ${msg}` };
    }
  }
  if (["integer", "float", "number"].includes(dataType)) {
    if (text === "") return { ok: true, value: "" };
    const n = Number(text);
    if (!Number.isFinite(n)) return { ok: false, error: "Enter a number" };
    if (dataType === "integer" && !Number.isInteger(n))
      return { ok: false, error: "Enter a whole number" };
    return { ok: true, value: n };
  }
  if (dataType === "boolean") {
    return { ok: true, value: String(text) === "true" ? "true" : "false" };
  }
  if (dataType === "datetime") {
    // The cell API stores naive wall time, not UTC. Keep the picker's clock
    // unchanged; its T separator is not accepted by DateTimeFormatChoices.
    const local = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?$/.exec(text);
    if (local) {
      return { ok: true, value: `${local[1]} ${local[2]}:${local[3] ?? "00"}` };
    }
  }
  return { ok: true, value: text };
}

/** True when an edit leaves the stored value unchanged. */
export function sameValue(a, b) {
  const norm = (v) =>
    v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
  return norm(a) === norm(b);
}

/**
 * A YAML edit of a JSON field (R2 P6): parsed back to compact JSON text.
 * `{ok, value, commentsDropped}` or `{ok:false, error:"Invalid YAML: … (line 3)"}`.
 * @param {string} draft
 */
export function validateYamlEdit(draft) {
  const text = draft ?? "";
  if (!text.trim()) return { ok: true, value: "", commentsDropped: false };
  try {
    const parsed = parseYaml(text);
    return {
      ok: true,
      value: JSON.stringify(parsed ?? null),
      commentsDropped: /(^|\s)#/m.test(text),
    };
  } catch (e) {
    const line = e?.linePos?.[0]?.line;
    const msg = String(e?.message ?? e)
      .split("\n")[0]
      .replace(/ at line \d+, column \d+:?$/, "");
    return {
      ok: false,
      error: `Invalid YAML: ${msg}${line ? ` (line ${line})` : ""}`,
    };
  }
}

/** YAML text of a JSON value / JSON text (for the editor's YAML tab). */
export function toYamlText(value) {
  let v = value;
  if (typeof v === "string") {
    try {
      v = JSON.parse(v);
    } catch {
      return v;
    }
  }
  try {
    return yamlStringify(v ?? null);
  } catch {
    return typeof value === "string" ? value : "";
  }
}

/**
 * RV#13: the first syntax error in JSON text, with its 1-based line and
 * column — a small scanner, because engines' JSON.parse messages often
 * carry no position (`Unexpected token '}', "…" is not valid JSON`).
 * @param {string} text
 * @returns {null | {message: string, line: number, col: number}}
 */
export function jsonSyntaxError(text) {
  const s = String(text ?? "");
  let i = 0;
  const ws = () => {
    while (i < s.length && " \t\n\r".includes(s[i])) i += 1;
  };
  const fail = (message) => {
    const lines = s.slice(0, i).split("\n");
    const err = new Error(message);
    err.at = { line: lines.length, col: lines[lines.length - 1].length + 1 };
    throw err;
  };
  const shown = () => (i >= s.length ? "end of input" : `'${s[i]}'`);
  const str = () => {
    i += 1;
    while (i < s.length && s[i] !== '"') {
      if (s[i] === "\n") fail("Line break inside a string");
      i += s[i] === "\\" ? 2 : 1;
    }
    if (i >= s.length) fail("Unterminated string");
    i += 1;
  };
  const value = () => {
    ws();
    const c = s[i];
    if (c === "{") {
      i += 1;
      ws();
      if (s[i] === "}") return void (i += 1);
      for (;;) {
        ws();
        if (s[i] !== '"') fail(`Expected a quoted key, found ${shown()}`);
        str();
        ws();
        if (s[i] !== ":") fail(`Expected ':' after the key, found ${shown()}`);
        i += 1;
        value();
        ws();
        if (s[i] === ",") {
          i += 1;
          continue;
        }
        if (s[i] === "}") return void (i += 1);
        fail(`Expected ',' or '}', found ${shown()}`);
      }
    }
    if (c === "[") {
      i += 1;
      ws();
      if (s[i] === "]") return void (i += 1);
      for (;;) {
        value();
        ws();
        if (s[i] === ",") {
          i += 1;
          continue;
        }
        if (s[i] === "]") return void (i += 1);
        fail(`Expected ',' or ']', found ${shown()}`);
      }
    }
    if (c === '"') return str();
    const lit = s
      .slice(i)
      .match(/^(true|false|null|-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?)/);
    if (lit) return void (i += lit[0].length);
    fail(`Expected a value, found ${shown()}`);
  };
  try {
    value();
    ws();
    if (i < s.length) fail(`Unexpected ${shown()} after the value`);
    return null;
  } catch (e) {
    return { message: e.message, ...(e.at ?? { line: 1, col: 1 }) };
  }
}

/**
 * RV#13: the live footer of the field editor — characters, a rough token
 * estimate (~4 characters a token) and, for JSON fields, whether the text
 * parses (with the line/column of the first error).
 * @param {string} text
 * @param {string} [dataType]
 * @returns {{chars: number, tokens: number,
 *   json: null | {ok: true} | {ok: false, message: string, line: number, col: number}}}
 */
export function editStats(text, dataType) {
  const t = text ?? "";
  const chars = t.length;
  const tokens = chars ? Math.max(1, Math.round(chars / 4)) : 0;
  if (!JSON_TYPES.has(dataType)) return { chars, tokens, json: null };
  if (!t.trim()) return { chars, tokens, json: { ok: true } };
  const err = jsonSyntaxError(t);
  return { chars, tokens, json: err ? { ok: false, ...err } : { ok: true } };
}

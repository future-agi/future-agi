// W5-1 A2 · autocomplete for the expression input (W5-SCOPE §5 A2): fields,
// run refs, operators and values (with counts) for the word at the cursor.
// Pure; reads only the catalogue and the value hints it is given.
import { FUNCTIONS } from "./expressionParser";
import {
  RUN_WORDS,
  VERDICT_WORDS,
  keywordOf,
  tokenize,
} from "./expressionLexer";
import { lookupField, quoteName } from "./fieldCatalog";

export const MAX_SUGGESTIONS = 12;

const WORD_CHAR = /[A-Za-z0-9_.#·`]/;
const OPERATORS = [
  { label: "=", insert: "= " },
  { label: "!=", insert: "!= " },
  { label: "<", insert: "< " },
  { label: ">", insert: "> " },
  { label: "<=", insert: "<= " },
  { label: ">=", insert: ">= " },
  { label: "in (…)", insert: "in (" },
  { label: "not in (…)", insert: "not in (" },
  { label: "contains", insert: "contains " },
  { label: "startswith", insert: "startswith " },
  { label: "is empty", insert: "is empty " },
  { label: "is not empty", insert: "is not empty " },
];
const JOINERS = [
  { label: "and", insert: "and " },
  { label: "or", insert: "or " },
];
const COMPARE = /(!=|>=|<=|=|<|>|\bin\s*\(|,|\bcontains|\bstartswith)\s*$/i;

/** The word being typed: `{from, to, word}` around the cursor. */
export function wordAt(text, cursor) {
  let from = cursor;
  while (from > 0 && WORD_CHAR.test(text[from - 1])) from -= 1;
  let to = cursor;
  while (to < text.length && WORD_CHAR.test(text[to])) to += 1;
  return { from, to, word: text.slice(from, cursor) };
}

const COMPARE_OPS = ["=", "!=", "<", "<=", ">", ">="];
const isOp = (tok, v) => tok?.type === "op" && tok.value === v;
const segmentOf = (tok) => {
  if (tok?.type === "ident" || tok?.type === "name") return tok.value;
  if (tok?.type === "num" && Number.isInteger(tok.value))
    return String(tok.value);
  return null;
};

// Index of the last token of the field an operator follows, or -1.
// `<field> <cmp> ` · `<field> [not] contains|startswith ` ·
// `<field> [not] in (` · `<field> [not] in (v1, v2, `.
function operandEndBefore(toks) {
  let k = toks.length - 1;
  const last = toks[k];
  if (!last) return -1;
  if (last.type === "op" && COMPARE_OPS.includes(last.value)) return k - 1;
  const kw = keywordOf(last);
  if (kw === "contains" || kw === "startswith")
    return keywordOf(toks[k - 1]) === "not" ? k - 2 : k - 1;
  if (isOp(last, ",")) {
    // Back over the list's values to its "(" (list values hold no parens).
    while (k >= 0 && !isOp(toks[k], "(")) {
      if (isOp(toks[k], ")")) return -1;
      k -= 1;
    }
    if (k < 0) return -1;
  } else if (!isOp(last, "(")) return -1;
  if (keywordOf(toks[k - 1]) !== "in") return -1;
  return keywordOf(toks[k - 2]) === "not" ? k - 3 : k - 2;
}

// The run ref ending at toks[i] (`#2B`, `baseline`, `any`, `all`,
// `run("…")`) when one starts the field, else false.
function runRefEndsAt(toks, i) {
  const t = toks[i];
  if (t?.type === "runref") return true;
  if (
    t?.type === "ident" &&
    RUN_WORDS.includes(t.value.toLowerCase()) &&
    !isOp(toks[i - 1], ".")
  )
    return true;
  return (
    isOp(t, ")") &&
    toks[i - 1]?.type === "str" &&
    isOp(toks[i - 2], "(") &&
    toks[i - 3]?.type === "ident" &&
    toks[i - 3].value.toLowerCase() === "run"
  );
}

// The field named just before an operator: `metadata.language = ` → that
// field. One lexer pass, then a walk back over the tokens: linear in the
// text whatever it holds (no regex, so no backtracking on long lists).
function fieldBefore(text, catalog) {
  const lexed = tokenize(text);
  if (lexed.error) return null;
  const toks = lexed.tokens.slice(0, -1);
  let i = operandEndBefore(toks);
  const path = [];
  let wantRun = false;
  while (i >= 0) {
    const seg = segmentOf(toks[i]);
    if (seg == null) return null;
    path.push(seg);
    if (!isOp(toks[i - 1], ".")) break;
    if (runRefEndsAt(toks, i - 2)) {
      wantRun = true;
      break;
    }
    i -= 2;
  }
  if (!path.length) return null;
  return lookupField(catalog, path.reverse(), { wantRun }) ?? null;
}

const rank = (label, q) => {
  const l = label.toLowerCase();
  if (!q) return 1;
  if (l.startsWith(q)) return 0;
  const last = l.slice(l.lastIndexOf(".") + 1);
  if (last.startsWith(q)) return 1;
  return l.includes(q) ? 2 : -1;
};

function pick(items, q) {
  return items
    .map((it, i) => ({ ...it, r: rank(it.label, q), i }))
    .filter((it) => it.r >= 0)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, MAX_SUGGESTIONS)
    .map(({ r, i, ...it }) => it);
}

/**
 * Suggestions for the cursor position.
 * @param {string} text
 * @param {number} cursor
 * @param {object} catalog  fieldCatalog catalogue
 * @param {{values?: Object<string, Array<{value: string, count?: number}>>}} [hints]
 *   values per field name (metadata values with counts from the rows in view)
 * @returns {{from: number, to: number, items: Array<{label, insert, detail?, kind}>}}
 */
export function suggest(text, cursor, catalog, hints = {}) {
  const src = String(text ?? "");
  const at = Math.max(0, Math.min(cursor ?? src.length, src.length));
  const { from, to, word } = wordAt(src, at);
  const before = src.slice(0, from);
  const q = word.toLowerCase();

  // Value position: after an operator or inside in (…).
  if (COMPARE.test(before)) {
    const field = fieldBefore(before, catalog);
    const values = [];
    const seen = new Set();
    const add = (v, detail, kind = "value") => {
      const key = String(v).toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      const bare =
        typeof v !== "string" ||
        VERDICT_WORDS.includes(key) ||
        (field?.values ?? []).includes(v);
      values.push({
        label: String(v),
        insert: bare ? `${v} ` : `${JSON.stringify(v)} `,
        detail,
        kind,
      });
    };
    if (field) {
      (hints.values?.[field.name] ?? []).forEach((h) =>
        add(h.value, h.count != null ? `${h.count} rows` : undefined),
      );
      (field.values ?? []).forEach((v) => add(v));
      if (field.type === "eval")
        ["pass", "fail"].forEach((v) => add(v, "verdict"));
      if (field.type === "boolean") ["true", "false"].forEach((v) => add(v));
    }
    return { from, to, items: pick(values, q.replace(/^["']/, "")) };
  }

  // After a complete operand: operators, then and / or.
  const operandEnd =
    /([\w`)\]"'])\s+$/.test(before) &&
    !/\b(and|or|not|in|is)\s+$/i.test(before);
  if (operandEnd && !word) {
    const lastWord = /([\w.`#·]+)\s+$/.exec(before)?.[1] ?? "";
    const isField = !!lookupField(
      catalog,
      lastWord
        .replace(/^(#[^.]+|baseline|any|all)\./i, "")
        .replace(/`/g, "")
        .split("."),
      { wantRun: /^(#|baseline\.|any\.|all\.)/i.test(lastWord) },
    );
    return {
      from,
      to,
      items: (isField ? [...OPERATORS, ...JOINERS] : JOINERS).map((o) => ({
        ...o,
        kind: "operator",
      })),
    };
  }

  // A run ref, then its fields.
  const runPrefix =
    /^(#[0-9]+[A-Za-z]+(?:·\d+)?|baseline|any|all)\.(.*)$/i.exec(word);
  const runFields = catalog.fields.filter((f) => f.scope === "run");
  if (runPrefix) {
    const rest = runPrefix[2].toLowerCase();
    const items = catalog.fields
      .filter((f) => f.scope === "run")
      .map((f) => ({
        label: `${runPrefix[1]}.${quoteName(f.name)}`,
        insert: `${runPrefix[1]}.${quoteName(f.name)} `,
        detail: f.doc,
        kind: "field",
      }));
    return {
      from,
      to,
      items: pick(items, `${runPrefix[1].toLowerCase()}.${rest}`),
    };
  }
  const runs = catalog.runs ?? [];
  const refItems = [
    ...runs.map((r) => ({
      label: r.label,
      insert: `${r.label}.`,
      detail: r.id === catalog.baselineId ? "Baseline run" : "Run",
      kind: "run",
    })),
    ...(runs.length >= 2
      ? [
          {
            label: "any.",
            insert: "any.",
            detail: "At least one run",
            kind: "run",
          },
          { label: "all.", insert: "all.", detail: "Every run", kind: "run" },
        ]
      : []),
    ...(catalog.baselineId
      ? [
          {
            label: "baseline.",
            insert: "baseline.",
            detail: "The baseline run",
            kind: "run",
          },
        ]
      : []),
  ];
  if (word.startsWith("#")) return { from, to, items: pick(refItems, q) };
  const rowItems = catalog.fields
    .filter((f) => f.scope === "row")
    .map((f) => ({
      label: quoteName(f.name),
      insert: `${quoteName(f.name)} `,
      detail: f.doc,
      kind: "field",
    }));
  const runItems = runFields.map((f) => ({
    label: quoteName(f.name),
    insert: `${quoteName(f.name)} `,
    detail: runs.length >= 2 ? `${f.doc} · any candidate` : f.doc,
    kind: "field",
  }));
  const fnItems = Object.keys(FUNCTIONS).map((fn) => ({
    label: `${fn}(…)`,
    insert: `${fn}(`,
    detail: "Function",
    kind: "function",
  }));
  const notItem = [{ label: "not", insert: "not ", kind: "operator" }];
  return {
    from,
    to,
    items: pick(
      [...rowItems, ...runItems, ...refItems, ...fnItems, ...notItem],
      q,
    ),
  };
}

/** Apply a suggestion: `{text, cursor}`. */
export function applySuggestion(text, { from, to }, item) {
  const next = `${text.slice(0, from)}${item.insert}${text.slice(to).replace(/^\s+/, "")}`;
  return { text: next, cursor: from + item.insert.length };
}

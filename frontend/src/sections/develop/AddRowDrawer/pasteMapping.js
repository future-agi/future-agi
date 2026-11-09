// IT-R3 G5 P4: map a pasted sheet's headers onto a dataset — each header →
// an existing column, a metadata key, a new column, or Skip — guessed by
// name and meaning. Pure.
import { INPUT_SYNONYMS } from "src/sections/common/RunSources/runOnRows";
import {
  enforceModeOf,
  validateRow,
} from "src/sections/common/Values/schemaValidate";
import { applyTransforms } from "src/sections/common/Values/transforms";

export const CREATE = "__create__";
export const SKIP = "__skip__";
export const META_PREFIX = "metadata.";

const norm = (s) =>
  String(s ?? "")
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");

const INPUT_WORDS = new Set([
  ...INPUT_SYNONYMS,
  "user_request",
  "request",
  "user_input",
  "customer_question",
  "ask",
]);
const EXPECTED_WORDS = new Set([
  "answer",
  "expected",
  "expected_answer",
  "expected_output",
  "expected_reply",
  "expected_response",
  "reference",
  "ground_truth",
  "ideal",
  "ideal_answer",
  "reply",
  "response",
  "target",
  "gold",
]);
const META_WORDS = new Set([
  "category",
  "tag",
  "tags",
  "intent",
  "topic",
  "label",
  "source",
  "tier",
  "notes",
  "note",
  "priority",
  "language",
  "locale",
  "channel",
]);

const roleOf = (c) => String(c?.role ?? c?.column_role ?? "").toLowerCase();
const nameOf = (c) => c?.name ?? c?.headerName ?? "";
const idOf = (c) => c?.id ?? c?.field;

/**
 * The guessed target of one header: a column id, `metadata.<key>`, CREATE.
 * Same name first, then meaning (question → the input column, answer → the
 * expected column, category → metadata.category).
 */
export function guessTarget(header, columns = []) {
  const h = norm(header);
  if (!h) return SKIP;
  const same = columns.find((c) => norm(nameOf(c)) === h);
  if (same) return idOf(same);
  if (INPUT_WORDS.has(h)) {
    const col =
      columns.find((c) => roleOf(c) === "input") ??
      columns.find((c) => INPUT_WORDS.has(norm(nameOf(c))));
    if (col) return idOf(col);
  }
  if (EXPECTED_WORDS.has(h)) {
    const col =
      columns.find((c) => roleOf(c) === "expected") ??
      columns.find((c) => EXPECTED_WORDS.has(norm(nameOf(c))));
    if (col) return idOf(col);
  }
  if (META_WORDS.has(h)) return `${META_PREFIX}${h}`;
  return CREATE;
}

/**
 * Header → target for every header; a column is used once (a second header
 * that guesses the same column becomes Create column). When nothing
 * matches, every header defaults to Create column.
 */
export function defaultMapping(headers = [], columns = []) {
  const used = new Set();
  const out = {};
  headers.forEach((h) => {
    let t = guessTarget(h, columns);
    if (t !== CREATE && t !== SKIP && !String(t).startsWith(META_PREFIX)) {
      if (used.has(t)) t = CREATE;
      else used.add(t);
    }
    out[h] = t;
  });
  return out;
}

/** `{fills: [names], metadata: [keys], create: [headers], skip: [headers]}`. */
export function mappingSummary(headers = [], mapping = {}, columns = []) {
  const name = (id) => nameOf(columns.find((c) => idOf(c) === id)) || id;
  const out = { fills: [], metadata: [], create: [], skip: [] };
  headers.forEach((h) => {
    const t = mapping[h] ?? SKIP;
    if (t === SKIP) out.skip.push(h);
    else if (t === CREATE) out.create.push(h);
    else if (String(t).startsWith(META_PREFIX))
      out.metadata.push(String(t).slice(META_PREFIX.length));
    else out.fills.push(name(t));
  });
  return out;
}

/**
 * Rows as change-set `add_row` parts: `{cells: {[colId]: v}, metadata}`.
 * `createdIds` maps a header to the id of the column created for it.
 */
export function mappedRows(
  rows = [],
  headers = [],
  mapping = {},
  createdIds = {},
) {
  return rows.map((cells) => {
    const out = { cells: {}, metadata: {} };
    headers.forEach((h, i) => {
      const t = mapping[h] ?? SKIP;
      const v = cells?.[i] ?? "";
      if (t === SKIP) return;
      if (t === CREATE) {
        if (createdIds[h]) out.cells[createdIds[h]] = v;
        return;
      }
      if (String(t).startsWith(META_PREFIX)) {
        if (v !== "") out.metadata[String(t).slice(META_PREFIX.length)] = v;
        return;
      }
      out.cells[t] = v;
    });
    return out;
  });
}

const plural = (n, one, many = `${one}s`) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** `Add 2 rows · 3 new columns` (the button states the result). */
export function addLabel(n, summary) {
  const parts = [`Add ${plural(n, "row")}`];
  if (summary?.create?.length)
    parts.push(plural(summary.create.length, "new column"));
  return parts.join(" · ");
}

/** Why Add is off (null when it can run). */
export function blockedReason({ text, error, n, summary }) {
  if (!String(text ?? "").trim()) return "Paste rows first";
  if (error) return error;
  if (!n) return "No rows under the header line";
  const used =
    (summary?.fills?.length ?? 0) +
    (summary?.metadata?.length ?? 0) +
    (summary?.create?.length ?? 0);
  if (!used)
    return "Every column is set to Skip. Pick a column for at least one.";
  return null;
}

const JSON_TYPES = new Set([
  "json",
  "conversation",
  "checks",
  "trace",
  "array",
]);
const NUMBER_TYPES = new Set(["float", "integer"]);

/**
 * W3-2 Reject on paste: indexes of mapped rows that break the dataset's
 * declared schema (the ONE validator), `{index, issues}`; [] unless the
 * schema rejects. Cells are keyed by column id.
 * @param {{cells: object}[]} rows  mappedRows() output
 * @param {{id: string, name: string, data_type?: string}[]} columns
 * @param {{declared?: object, enforce?: string|boolean}|null} schema
 */
export function schemaRejected(rows = [], columns = [], schema = null) {
  if (enforceModeOf(schema) !== "reject" || !schema?.declared) return [];
  const cols = columns.map((c) => ({
    id: c.id ?? c.field,
    name: nameOf(c),
    json: JSON_TYPES.has(String(c.data_type ?? "").toLowerCase()),
    number: NUMBER_TYPES.has(String(c.data_type ?? "").toLowerCase()),
  }));
  const out = [];
  rows.forEach((r, index) => {
    const byName = {};
    cols.forEach((c) => {
      if (c.id in (r.cells ?? {})) byName[c.name] = r.cells[c.id];
    });
    const { issues } = validateRow(byName, schema.declared, {
      columns: cols.filter((c) => c.name in byName),
    });
    if (issues.length) out.push({ index, issues });
  });
  return out;
}

/**
 * W3-4: pasted rows after the dataset's transformations (cells by column
 * id) and the notes (`Removed 1 system message (conversation)`).
 */
export function transformRows(rows = [], columns = [], schema = null) {
  if (!schema?.transforms?.length) return { rows, notes: [] };
  const nameOf = new Map(columns.map((c) => [c.id ?? c.field, nameOf2(c)]));
  const idOf = new Map(columns.map((c) => [nameOf2(c), c.id ?? c.field]));
  const notes = [];
  const out = rows.map((r) => {
    const byName = {};
    Object.entries(r.cells ?? {}).forEach(([id, v]) => {
      byName[nameOf.get(id) ?? id] = v;
    });
    const t = applyTransforms(byName, schema);
    notes.push(...t.notes);
    const cells = {};
    Object.entries(t.cells).forEach(([name, v]) => {
      cells[idOf.get(name) ?? name] = v;
    });
    return { ...r, cells };
  });
  return { rows: out, notes };
}
const nameOf2 = (c) => c?.name ?? c?.headerName ?? "";

// A structured list filter (W9-SCOPE §5 A2), generic over a page's fields.
// Conditions use the platform filter shape:
//   {column_id, filter_config: {filter_type, filter_op, filter_value}}
// Conditions are joined by AND; the values of one condition are OR (`in`).
// Operators come only from the filter contract (filter-contract.generated:
// FILTER_TYPE_ALLOWED_OPS, LIST_FILTER_OPS, RANGE_FILTER_OPS,
// NO_VALUE_FILTER_OPS). Pure: no React, no fetch.
//
// A field: {id, label, type, multi?, ops?}
//   type     "text" | "categorical" | "number" | "datetime" | "boolean"
//            | "percent" (a 0..1 rate shown as %) | "points" (± pts)
//            | "folder" (is / is under / is empty)
//   multi    the item has several values (tags, agents): `in` = any of
//   ops      a narrower operator list (a subset of the contract's)
import {
  FILTER_TYPE_ALLOWED_OPS,
  LIST_FILTER_OPS,
  NO_VALUE_FILTER_OPS,
  RANGE_FILTER_OPS,
} from "src/api/contracts/filter-contract.generated";

/** The filter-contract type a field's values are filtered as. */
export const CONTRACT_TYPE = Object.freeze({
  text: "text",
  categorical: "categorical",
  number: "number",
  percent: "number",
  points: "number",
  datetime: "datetime",
  boolean: "boolean",
  folder: "text",
});

// Operators each field type offers, in menu order. Every one is checked
// against the contract (opsFor), so a type can only narrow the contract.
const TYPE_OPS = Object.freeze({
  text: ["contains", "not_contains", "equals", "not_equals", "starts_with"],
  categorical: ["in", "not_in", "is_null", "is_not_null"],
  number: [
    "less_than",
    "greater_than",
    "less_than_or_equal",
    "greater_than_or_equal",
    "equals",
    "between",
  ],
  percent: ["less_than", "greater_than", "between"],
  points: ["less_than", "greater_than", "between"],
  datetime: ["greater_than", "less_than", "between"],
  boolean: ["equals"],
  // is / is under / is empty
  folder: ["equals", "starts_with", "is_null"],
});

/** Words for each operator (chips and the operator menu). */
export const OP_WORDS = Object.freeze({
  equals: "is",
  not_equals: "is not",
  in: "is any of",
  not_in: "is none of",
  contains: "contains",
  not_contains: "does not contain",
  starts_with: "starts with",
  less_than: "less than",
  greater_than: "greater than",
  less_than_or_equal: "at most",
  greater_than_or_equal: "at least",
  between: "between",
  not_between: "not between",
  is_null: "is empty",
  is_not_null: "is set",
});
const OP_SYMBOL = Object.freeze({
  less_than: "<",
  greater_than: ">",
  less_than_or_equal: "≤",
  greater_than_or_equal: "≥",
});
const DATE_WORDS = Object.freeze({
  greater_than: "after",
  less_than: "before",
});

const NO_VALUE = new Set(NO_VALUE_FILTER_OPS);
const LIST_OPS = new Set(LIST_FILTER_OPS);
const RANGE_OPS = new Set(RANGE_FILTER_OPS);

export const isNoValueOp = (op) => NO_VALUE.has(op);
export const isListOp = (op) => LIST_OPS.has(op);
export const isRangeOp = (op) => RANGE_OPS.has(op);
const NUMERIC = new Set(["number", "percent", "points"]);
export const isNumericField = (field) => NUMERIC.has(field?.type);

/** The operator word for a field (folder `starts_with` = "is under"). */
export function opWord(field, op) {
  if (field?.type === "folder") {
    if (op === "starts_with") return "is under";
    if (op === "is_null") return "is empty";
  }
  if (field?.type === "datetime" && DATE_WORDS[op]) return DATE_WORDS[op];
  return OP_WORDS[op] ?? op;
}

/** The operators a field offers, each allowed by the filter contract. */
export function opsFor(field) {
  const contractType = CONTRACT_TYPE[field?.type] ?? "text";
  const allowed = FILTER_TYPE_ALLOWED_OPS[contractType] ?? [];
  const wanted = field?.ops ?? TYPE_OPS[field?.type] ?? TYPE_OPS.text;
  return wanted.filter((op) => allowed.includes(op));
}

/** A new condition for a field, with its first operator. */
export function newCondition(field) {
  const op = opsFor(field)[0];
  return {
    column_id: field.id,
    filter_config: {
      filter_type: CONTRACT_TYPE[field.type] ?? "text",
      filter_op: op,
      filter_value: emptyValueFor(field, op),
    },
  };
}

/** The empty value an operator starts with. */
export function emptyValueFor(field, op) {
  if (isNoValueOp(op)) return null;
  if (isListOp(op)) return [];
  if (isRangeOp(op)) return [null, null];
  if (field?.type === "boolean") return true;
  return null;
}

const numberish = (v) =>
  v !== "" && v !== null && v !== undefined && Number.isFinite(Number(v));
const validDate = (v) =>
  v !== null && v !== undefined && v !== "" && !Number.isNaN(Date.parse(v));

/** A field by id. */
export function fieldById(fields, id) {
  return (fields ?? []).find((f) => f.id === id) ?? null;
}

/**
 * The first problem of one condition, as a sentence, or null.
 * @param {object} cond
 * @param {object[]} fields  the page's fields (metadata fields included)
 * @param {{unknownSentence?: (id: string) => string}} [opts]
 */
export function conditionProblem(cond, fields, opts = {}) {
  const id = cond?.column_id;
  const field = fieldById(fields, id);
  if (!field)
    return opts.unknownSentence
      ? opts.unknownSentence(id)
      : `Unknown field “${id}”.`;
  const op = cond?.filter_config?.filter_op;
  if (!opsFor(field).includes(op))
    return `“${OP_WORDS[op] ?? op}” is not an operator for ${field.label}.`;
  if (isNoValueOp(op)) return null;
  const v = cond.filter_config.filter_value;
  const word = opWord(field, op);
  const numeric = isNumericField(field);
  if (isListOp(op)) {
    if (!Array.isArray(v) || v.length === 0)
      return `“${word}” needs at least one value for ${field.label}.`;
    return null;
  }
  if (isRangeOp(op)) {
    if (!Array.isArray(v) || v.length !== 2)
      return `“${word}” needs two values for ${field.label}.`;
    if (numeric && !(numberish(v[0]) && numberish(v[1])))
      return `“${word}” needs two numbers for ${field.label}.`;
    if (field.type === "datetime" && !(validDate(v[0]) && validDate(v[1])))
      return `“${word}” needs two dates for ${field.label}.`;
    return null;
  }
  if (numeric && !numberish(v))
    return `“${word}” needs a number for ${field.label}.`;
  if (field.type === "datetime" && !validDate(v))
    return `“${word}” needs a date for ${field.label}.`;
  if (field.type === "boolean" && typeof v !== "boolean")
    return `“${word}” needs true or false for ${field.label}.`;
  if (
    (field.type === "text" || field.type === "folder") &&
    (v === null || v === undefined || String(v).trim() === "")
  )
    return `“${word}” needs a value for ${field.label}.`;
  return null;
}

/** The first problem among conditions, or null. */
export function filtersProblem(filters, fields, opts) {
  if (!Array.isArray(filters)) return "Filters must be a list of conditions.";
  for (const c of filters) {
    const p = conditionProblem(c, fields, opts);
    if (p) return p;
  }
  return null;
}

/** Compact JSON for the URL key `f` ("" when there are none). */
export function serializeFilters(filters) {
  const list = (filters ?? []).filter(Boolean);
  if (!list.length) return "";
  return JSON.stringify(
    list.map((c) => ({
      column_id: c.column_id,
      filter_config: {
        filter_type: c.filter_config?.filter_type,
        filter_op: c.filter_config?.filter_op,
        filter_value: c.filter_config?.filter_value ?? null,
        ...(c.filter_config?.significant ? { significant: true } : {}),
      },
    })),
  );
}

/**
 * Conditions from the URL key `f`. Unreadable text gives `[]` with the
 * reason, so a bad link never breaks the page.
 * @returns {{filters: object[], error: string|null}}
 */
export function parseFilters(text) {
  if (!text) return { filters: [], error: null };
  try {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed))
      return { filters: [], error: "The filter in the link is not a list." };
    const filters = parsed.filter(
      (c) =>
        c &&
        typeof c.column_id === "string" &&
        c.filter_config &&
        typeof c.filter_config.filter_op === "string",
    );
    return {
      filters,
      error:
        filters.length === parsed.length
          ? null
          : "Some conditions in the link were not readable and were left out.",
    };
  } catch {
    return { filters: [], error: "The filter in the link is not readable." };
  }
}

const pct = (v) => `${Number((Number(v) * 100).toFixed(1))}%`;
const pts = (v) => {
  const n = Number(v);
  return `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n)} pts`;
};

/** One value as a chip shows it. `labelOf(field, value)` names values. */
export function valueText(field, value, labelOf) {
  if (value === null || value === undefined) return "";
  if (field.type === "percent") return pct(value);
  if (field.type === "points") return pts(value);
  if (field.type === "boolean") return value ? "true" : "false";
  if (field.type === "datetime") return String(value).slice(0, 10);
  return (labelOf && labelOf(field, value)) ?? String(value);
}

/**
 * A chip's words: `Tag is golden`, `Tag is any of golden, prod`,
 * `Last pass < 80%`, `Δ vs baseline < 0 pts (significant)`,
 * `Folder is under support/billing`, `Folder is empty`.
 */
export function chipText(cond, fields, labelOf) {
  const field = fieldById(fields, cond?.column_id);
  if (!field) return String(cond?.column_id ?? "");
  const op = cond.filter_config?.filter_op;
  const v = cond.filter_config?.filter_value;
  const sig = cond.filter_config?.significant ? " (significant)" : "";
  if (isNoValueOp(op)) return `${field.label} ${opWord(field, op)}${sig}`;
  if (isListOp(op)) {
    const values = (Array.isArray(v) ? v : [v]).map((x) =>
      valueText(field, x, labelOf),
    );
    // One value of a list operator reads "is" / "is not".
    const word =
      values.length === 1 ? (op === "in" ? "is" : "is not") : opWord(field, op);
    return `${field.label} ${word} ${values.join(", ")}${sig}`;
  }
  if (isRangeOp(op))
    return `${field.label} ${opWord(field, op)} ${valueText(field, v?.[0], labelOf)} and ${valueText(field, v?.[1], labelOf)}${sig}`;
  if (isNumericField(field) && OP_SYMBOL[op])
    return `${field.label} ${OP_SYMBOL[op]} ${valueText(field, v, labelOf)}${sig}`;
  return `${field.label} ${opWord(field, op)} ${valueText(field, v, labelOf)}${sig}`;
}

/** Conditions without the one at `index`. */
export const removeAt = (filters, index) =>
  (filters ?? []).filter((_, i) => i !== index);

/** Conditions with the one at `index` replaced. */
export const replaceAt = (filters, index, cond) =>
  (filters ?? []).map((c, i) => (i === index ? cond : c));

// Per-row schema validation (R2-SPEC P6 §3.4): a small JSON-Schema subset —
// `type`, `required`, `properties`, `items`, `enum`, `minItems`,
// `additionalProperties: false`. Messages name the FA path:
//   conversation[2].role must be one of system, user, assistant, tool
//   expected is not valid JSON
// Pure; never throws.
import isEqual from "lodash/isEqual";
import { formatPath } from "./faPath";

const TYPE_LABEL = {
  object: "an object",
  array: "a list",
  string: "text",
  number: "a number",
  integer: "a whole number",
  boolean: "true or false",
  null: "empty",
};

const typeOf = (v) => {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return "array";
  if (typeof v === "number") return Number.isInteger(v) ? "integer" : "number";
  return typeof v;
};

const matchesType = (v, t) => {
  const actual = typeOf(v);
  if (t === "number") return actual === "number" || actual === "integer";
  return actual === t;
};

const pathText = (steps) => formatPath(null, steps);

/**
 * Validate one value against a schema.
 * @param {*} value
 * @param {object} schema
 * @param {Array} [steps]  FA path steps of `value` (for messages)
 * @returns {{path: string, message: string}[]}
 */
export function validateValue(value, schema, steps = []) {
  const issues = [];
  if (!schema || typeof schema !== "object") return issues;
  const at = pathText(steps) || "value";
  const push = (message, s = steps) =>
    issues.push({ path: pathText(s) || "value", message });

  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => matchesType(value, t))) {
      push(
        `${at} must be ${types.map((t) => TYPE_LABEL[t] ?? t).join(" or ")}`,
      );
      return issues;
    }
  }
  if (
    Array.isArray(schema.enum) &&
    !schema.enum.some((candidate) => isEqual(candidate, value))
  ) {
    push(`${at} must be one of ${schema.enum.join(", ")}`);
    return issues;
  }
  if (Array.isArray(value)) {
    if (typeof schema.minItems === "number" && value.length < schema.minItems)
      push(
        `${at} must have at least ${schema.minItems} ${schema.minItems === 1 ? "item" : "items"}`,
      );
    if (schema.items)
      value.forEach((item, i) =>
        issues.push(
          ...validateValue(item, schema.items, [
            ...steps,
            { type: "index", index: i },
          ]),
        ),
      );
  } else if (value && typeof value === "object") {
    (schema.required ?? []).forEach((k) => {
      if (!(k in value))
        push(`${pathText([...steps, { type: "key", key: k }])} is required`, [
          ...steps,
          { type: "key", key: k },
        ]);
    });
    const props = schema.properties ?? {};
    Object.entries(value).forEach(([k, v]) => {
      const ks = [...steps, { type: "key", key: k }];
      if (props[k]) issues.push(...validateValue(v, props[k], ks));
      else if (schema.additionalProperties === false)
        push(`${pathText(ks)} is not allowed`, ks);
    });
  }
  return issues;
}

const parseCell = (raw, jsonColumn) => {
  if (!jsonColumn || typeof raw !== "string") return { ok: true, value: raw };
  const t = raw.trim();
  if (!t) return { ok: true, value: null };
  try {
    return { ok: true, value: JSON.parse(t) };
  } catch {
    return { ok: false };
  }
};

/**
 * Validate one row against a declared row schema (`{type:"object",
 * properties:{<column name>: schema}, required:[…]}`). Only columns that
 * exist are checked; a JSON column whose text does not parse reads
 * `<column> is not valid JSON`.
 * @param {Object<string, *>} valuesByName  stored values by column name
 * @param {object} declared
 * @param {{columns?: {name: string, json?: boolean, number?: boolean, boolean?: boolean}[]}} [ctx]
 * @returns {{ok: boolean, issues: {path: string, message: string}[]}}
 */
export function validateRow(valuesByName, declared, { columns = [] } = {}) {
  const issues = [];
  const props = declared?.properties ?? {};
  const required = new Set(declared?.required ?? []);
  const byName = new Map(columns.map((c) => [c.name, c]));
  required.forEach((name) => {
    const value = valuesByName[name];
    if (
      !byName.has(name) ||
      value === null ||
      value === undefined ||
      value === ""
    )
      issues.push({ path: name, message: `${name} is required` });
  });
  columns.forEach((col) => {
    const name = col.name;
    const schema = props[name];
    if (!schema) {
      if (declared?.additionalProperties === false)
        issues.push({ path: name, message: `${name} is not allowed` });
      return;
    }
    const raw = valuesByName[name];
    const empty = raw === null || raw === undefined || raw === "";
    if (empty && required.has(name)) return;
    const parsed = parseCell(empty ? null : raw, col.json);
    if (!parsed.ok) {
      issues.push({ path: name, message: `${name} is not valid JSON` });
      return;
    }
    let value = parsed.value;
    if (col.number && typeof value === "string") {
      const t = value.trim();
      if (/^-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) value = Number(t);
    }
    if (col.boolean && typeof value === "string") {
      if (value.toLowerCase() === "true") value = true;
      else if (value.toLowerCase() === "false") value = false;
    }
    issues.push(...validateValue(value, schema, [{ type: "key", key: name }]));
  });
  return { ok: issues.length === 0, issues };
}

/**
 * Group issues across rows by message: `[{message, path, rows:[rowId]}]`,
 * most rows first.
 * @param {{rowId: string, issues: {path: string, message: string}[]}[]} results
 */
export function groupIssues(results) {
  const groups = new Map();
  results.forEach(({ rowId, issues }) =>
    (issues ?? []).forEach((issue) => {
      const g = groups.get(issue.message) ?? {
        message: issue.message,
        path: issue.path,
        rows: [],
      };
      if (!g.rows.includes(rowId)) g.rows.push(rowId);
      groups.set(issue.message, g);
    }),
  );
  return [...groups.values()].sort((a, b) => b.rows.length - a.rows.length);
}

/**
 * Problems with a schema itself (Monaco's `Invalid schema` line), or [].
 * @param {*} schema
 */
export function schemaProblems(schema) {
  const out = [];
  const known = new Set([
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
  ]);
  const keywords = new Set([
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "minItems",
    "additionalProperties",
    "title",
    "description",
    "$schema",
  ]);
  const walk = (s, at) => {
    if (!s || typeof s !== "object" || Array.isArray(s)) {
      out.push(`${at || "schema"} must be an object`);
      return;
    }
    Object.keys(s).forEach((key) => {
      if (!keywords.has(key))
        out.push(`${at || "schema"}: unsupported keyword "${key}"`);
    });
    if (s.type !== undefined) {
      const types = Array.isArray(s.type) ? s.type : [s.type];
      types.forEach((t) => {
        if (!known.has(t)) out.push(`${at || "schema"}: unknown type "${t}"`);
      });
    }
    if (s.enum !== undefined && !Array.isArray(s.enum))
      out.push(`${at || "schema"}: enum must be a list`);
    if (s.required !== undefined && !Array.isArray(s.required))
      out.push(`${at || "schema"}: required must be a list`);
    if (s.properties !== undefined) {
      if (typeof s.properties !== "object" || Array.isArray(s.properties))
        out.push(`${at || "schema"}: properties must be an object`);
      else
        Object.entries(s.properties).forEach(([k, v]) =>
          walk(v, at ? `${at}.${k}` : k),
        );
    }
    if (s.items !== undefined) walk(s.items, `${at || "schema"}[*]`);
  };
  walk(schema, "");
  return out;
}

// W3-2 Enforce: `dataset_config.schema.enforce` is `off | check | reject`.
export const ENFORCE_MODES = Object.freeze(["off", "check", "reject"]);

/**
 * The enforce mode of a stored schema: `reject`, `check` (boolean `true`
 * from before W3 reads as `check`), else `off`. Shared by the Schema drawer
 * and the mock so both read a stored schema the same way.
 * @param {{enforce?: boolean|string}|null} schema
 */
export function enforceModeOf(schema) {
  const e = schema?.enforce;
  if (e === "reject") return "reject";
  if (e === true || e === "check") return "check";
  return "off";
}

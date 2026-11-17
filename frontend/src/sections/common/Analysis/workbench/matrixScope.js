// W5-2 B1 · Explore's group by an expression: the matrix-row scope of the one
// expression language (common/filters/expression.js). A matrix row (R3
// `shape=matrix`) carries the dataset's user columns by name (`values`) and
// its group keys (`group_values`: category, difficulty, split, source and the
// metadata keys). The field names are the Data grid's (A0 gridCatalog): a
// column by its name, `metadata.<key>`, `split`, `tags`, `row`, `id`; a JSON
// column also opens as a path (`start_state.orders.0.status`). Grouping is
// per row, so per-run fields (outputs, scores) belong in a filter instead.
// Pure.
import { resolvePath } from "src/sections/common/Values/faPath";
import {
  compileExpression,
  evaluateExpression,
} from "src/sections/common/filters/expression";
import { MISSING } from "./workbenchModel";

const GROUP_KEYS = ["category", "difficulty", "split", "source"];

const typeOfValues = (vals) => {
  const seen = vals.filter((v) => v != null && v !== "");
  if (!seen.length) return "any";
  if (seen.every((v) => typeof v === "number")) return "number";
  if (seen.every((v) => typeof v === "boolean")) return "boolean";
  if (seen.every((v) => typeof v === "string")) return "text";
  if (seen.every((v) => Array.isArray(v))) return "list";
  if (seen.every((v) => typeof v === "object")) return "json";
  return "any";
};

const CATALOGS = new WeakMap();

/** The catalogue of a matrix (cached per matrix object). */
export function matrixCatalog(matrix) {
  if (matrix && CATALOGS.has(matrix)) return CATALOGS.get(matrix);
  const rows = matrix?.rows ?? [];
  const colNames = [
    ...new Set(rows.flatMap((r) => Object.keys(r.values ?? {}))),
  ];
  const metaKeys = [
    ...new Set(rows.flatMap((r) => Object.keys(r.group_values ?? {}))),
  ].filter((k) => !GROUP_KEYS.includes(k));
  const fields = [];
  const prefixes = [];
  colNames.forEach((name) => {
    const type = typeOfValues(rows.map((r) => r.values?.[name]));
    fields.push({
      name,
      scope: "row",
      type,
      read: { k: "value", col: name },
      doc: "Column",
    });
    if (type === "json")
      prefixes.push({
        prefix: `${name}.`,
        scope: "row",
        type: "any",
        read: { k: "value_path", col: name },
      });
  });
  metaKeys.forEach((key) =>
    fields.push({
      name: `metadata.${key}`,
      scope: "row",
      type: typeOfValues(rows.map((r) => r.group_values?.[key])),
      read: { k: "metadata", key },
      doc: "Metadata",
    }),
  );
  ["category", "difficulty"].forEach((key) => {
    if (colNames.includes(key)) return;
    if (!rows.some((r) => r.group_values?.[key] != null)) return;
    fields.push({
      name: key,
      scope: "row",
      type: "text",
      read: { k: "metadata", key },
      doc: key === "category" ? "Category" : "Difficulty",
    });
  });
  fields.push(
    {
      name: "split",
      scope: "row",
      type: "list",
      read: { k: "split" },
      doc: "Splits the row is in",
    },
    {
      name: "tags",
      scope: "row",
      type: "list",
      read: { k: "tags" },
      doc: "Row tags",
    },
    {
      name: "source",
      scope: "row",
      type: "text",
      read: { k: "source" },
      doc: "Where the row came from",
    },
    {
      name: "row",
      scope: "row",
      type: "number",
      read: { k: "row" },
      doc: "Row number",
    },
    {
      name: "id",
      scope: "row",
      type: "text",
      read: { k: "id" },
      doc: "Row id",
    },
  );
  const catalog = {
    kind: "grid",
    fields,
    prefixes,
    runs: [],
    baselineId: null,
    custom: [],
  };
  if (matrix) CATALOGS.set(matrix, catalog);
  return catalog;
}

const listOf = (v) =>
  v == null || v === ""
    ? []
    : Array.isArray(v)
      ? v
      : String(v)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);

function readRow(row, field) {
  const rd = field.read;
  switch (rd.k) {
    case "value":
      return row.values?.[rd.col] ?? null;
    case "value_path": {
      const hit = resolvePath(
        row.values?.[rd.col],
        (field.rest ?? []).map((seg) =>
          /^\d+$/.test(seg)
            ? { type: "index", index: Number(seg) }
            : { type: "key", key: seg },
        ),
      );
      return hit.found ? (hit.multi ? hit.values : hit.value) : null;
    }
    case "metadata":
      return row.group_values?.[rd.key] ?? null;
    case "split":
      return listOf(row.group_values?.split);
    case "tags":
      return listOf(row.tags ?? row.group_values?.tags);
    case "source":
      return row.group_values?.source ?? row.source ?? null;
    case "row":
      return row.n ?? null;
    case "id":
      return row.row_id ?? null;
    default:
      return null;
  }
}

/** The evaluation scope of one matrix row. */
export const matrixRowScope = (row, catalog) => ({
  catalog,
  runIds: [],
  baselineId: null,
  readRow: (field) => readRow(row, field),
  readRun: () => null,
});

/**
 * Compile a grouping expression against a matrix.
 * @returns {{ast, refs} | {error: {message, at, length}}}
 */
export function compileGroupExpression(text, matrix) {
  const src = String(text ?? "").trim();
  if (!src)
    return { error: { message: "Type an expression.", at: 0, length: 1 } };
  return compileExpression(src, matrixCatalog(matrix));
}

/** A value as a group name: `true`, `enterprise`, `(missing)`. */
export function groupNameOf(v) {
  if (v == null || v === "" || (typeof v === "number" && !Number.isFinite(v)))
    return MISSING;
  if (Array.isArray(v)) return v.length ? null : MISSING;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** A value's group names: a list puts a row in each of its values. */
export const groupNamesOf = (v) =>
  Array.isArray(v)
    ? v.length
      ? [...new Set(v.map((x) => groupNameOf(x) ?? MISSING))]
      : [MISSING]
    : [groupNameOf(v)];

/**
 * `row → group names` for an expression (a list value puts a row in each of
 * its groups, as Split and Tag do). Evaluated once per row. An expression
 * that does not compile puts every row in one `(invalid expression)` group.
 */
export function expressionGrouper(matrix, text) {
  const compiled = compileGroupExpression(text, matrix);
  const catalog = matrixCatalog(matrix);
  const cache = new Map();
  return (row) => {
    if (compiled.error) return ["(invalid expression)"];
    const key = row.row_id ?? row;
    if (cache.has(key)) return cache.get(key);
    let v;
    try {
      v = evaluateExpression(compiled.ast, matrixRowScope(row, catalog));
    } catch {
      v = null;
    }
    const names = groupNamesOf(v);
    cache.set(key, names);
    return names;
  };
}

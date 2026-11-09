// W3-10 · an Expression column on a dataset (W3 D8; W5-SCOPE §5 A5): the
// pure value function the Data tab's dynamic columns call
// (dynamicColumns.dynamicValue("expression", {expr}, ctx) →
// expressionColumnValue(expr, ctx.read, {columns, row})). One evaluator: the
// grid scope over the expression language. Result shape as dynamicValue:
//   {value, status: "pass" | "error", error?, data_type, trace}
import {
  evaluateExpression,
  fieldOf,
  parseExpression,
  validateExpression,
} from "./expression";
import { gridCatalogFor, gridRowScope } from "./gridScope";

export const CELL_ERROR_PREFIX = "Error · ";

/** `Unknown field “x”. Did you mean y?` → `Unknown column “x”` (+ the hint apart). */
export function columnErrorText(message) {
  const m = /^Unknown field “([^”]+)”\.(?:\s*(Did you mean .*))?$/.exec(
    message ?? "",
  );
  if (!m) return { error: message ?? "", hint: null };
  return { error: `Unknown column “${m[1]}”`, hint: m[2] ?? null };
}

/** The grid data type of a computed value. */
export function dataTypeOf(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number")
    return Number.isInteger(value) ? "integer" : "float";
  if (typeof value === "object") return "json";
  return "text";
}

/** The grid data type of a column from its cells (nulls ignored). */
export function columnDataType(values) {
  const types = new Set((values ?? []).map(dataTypeOf).filter(Boolean));
  if (!types.size) return "text";
  if (types.size === 1) return [...types][0];
  if ([...types].every((t) => t === "integer" || t === "float")) return "float";
  return "text";
}

// One ctx (and so one catalogue) per columns array.
const CTXS = new WeakMap();
const ctxFor = (columns) => {
  let ctx = CTXS.get(columns);
  if (!ctx) {
    ctx = { columns };
    CTXS.set(columns, ctx);
  }
  return ctx;
};

/** Check an expression against the dataset's columns: null or {error, hint, at, length}. */
export function expressionColumnError(expr, columns = []) {
  const parsed = parseExpression(String(expr ?? ""));
  if (parsed.error)
    return { ...parsed.error, error: parsed.error.message, hint: null };
  const v = validateExpression(parsed.ast, gridCatalogFor(ctxFor(columns)));
  if (!v.error) return null;
  return { ...v.error, ...columnErrorText(v.error.message) };
}

/**
 * The column ids an expression reads (HO D7): the re-run scope `changed`
 * fingerprints these cells, so editing one of them marks the row changed.
 * Fields that are not dataset columns (row number, splits, providers) are
 * left out; an expression that does not compile reads nothing.
 */
export function expressionColumnReads(expr, columns = []) {
  const parsed = parseExpression(String(expr ?? ""));
  if (parsed.error) return [];
  const catalog = gridCatalogFor(ctxFor(columns));
  const valid = validateExpression(parsed.ast, catalog);
  if (valid.error) return [];
  const ids = new Set();
  (valid.refs ?? []).forEach((ref) => {
    const id = fieldOf(catalog, ref.name)?.read?.id;
    if (id && columns.some((c) => c.id === id)) ids.add(id);
  });
  return [...ids];
}

/**
 * The value of an expression column on one dataset row.
 * @param {string} expr
 * @param {(ref: string) => *} read  the row's value of a column (id or name)
 * @param {{columns: Array, row?: {id, n, split_ids?}}} opts
 */
export function expressionColumnValue(
  expr,
  read,
  { columns = [], row = {} } = {},
) {
  const trace = { name: "Expression", input: { expr: String(expr ?? "") } };
  const problem = expressionColumnError(expr, columns);
  if (problem)
    return {
      value: null,
      status: "error",
      error: problem.error,
      data_type: null,
      trace: { ...trace, output: null, steps: [] },
    };
  const values = Object.fromEntries(
    columns
      .filter((c) => !c.deleted)
      .map((c) => [c.id, read(c.id) ?? read(c.name) ?? null]),
  );
  const scope = gridRowScope(
    {
      id: row.id ?? null,
      n: row.n ?? null,
      split_ids: row.split_ids ?? [],
      values,
    },
    ctxFor(columns),
  );
  const value = evaluateExpression(parseExpression(String(expr)).ast, scope);
  const out = value === undefined ? null : value;
  return {
    value: out,
    status: "pass",
    data_type: dataTypeOf(out),
    trace: { ...trace, output: out, steps: [] },
  };
}

/** The cell text of a result: the value, or `Error · Unknown column “x”`. */
export const expressionCellText = (res) =>
  res?.status === "error"
    ? `${CELL_ERROR_PREFIX}${res.error}`
    : res?.value === null || res?.value === undefined
      ? ""
      : typeof res.value === "string"
        ? res.value
        : JSON.stringify(res.value);

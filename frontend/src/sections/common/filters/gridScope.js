// W5-1 A3 · the grid scope (W5-SCOPE §5 A3): expressions over one dataset
// row (`{id, n, values{colId}, split_ids[], metadata{}, generated{colId:
// {value}}}`), and the row-field providers other areas register (A6:
// has_comment, assigned_to, language). Pure FE module; the mock's grid
// filter calls `gridExpressionMatchesRow` through w5GridScope.js.
import { compileExpression, matchesExpression } from "./expression";
import { gridCatalog } from "./fieldCatalog";

// ------------------------------------------------------------ providers
const PROVIDERS = new Map();
let providerVersion = 0;

/**
 * Register a row field every row scope offers (grid and results).
 * @param {string} name  e.g. `has_comment`
 * @param {{type: string, values?: string[], doc?: string,
 *   read: ({rowId, datasetId, row}) => *}} spec
 */
export function registerRowField(name, { type = "any", values, doc, read }) {
  if (typeof read !== "function")
    throw new Error(`registerRowField(${name}) needs read()`);
  PROVIDERS.set(name, { type, values, doc, read });
  providerVersion += 1;
}

/** Bumps on every (un)register: catalogues cached per ctx rebuild. */
export const providersVersion = () => providerVersion;

/** Drop a provider (tests). */
export function unregisterRowField(name) {
  if (PROVIDERS.delete(name)) providerVersion += 1;
}

/** Catalogue entries for the registered providers. */
export const providerFields = () =>
  [...PROVIDERS].map(([name, p]) => ({
    name,
    type: p.type,
    values: p.values,
    doc: p.doc ?? "Row field",
  }));

/** A provider's value for one row (null when nothing is registered). */
export function readProvider(name, args) {
  const p = PROVIDERS.get(name);
  if (!p) return null;
  try {
    return p.read(args) ?? null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------ catalogue
const CATALOGS = new WeakMap();

/** The grid catalogue for a ctx `{columns, datasetId, splitNames, customColumns}` (cached). */
export function gridCatalogFor(ctx = {}) {
  const hit = CATALOGS.get(ctx);
  if (hit && hit.version === providerVersion) return hit.catalog;
  const catalog = gridCatalog({
    columns: ctx.columns ?? [],
    rowFields: [...providerFields(), ...(ctx.rowFields ?? [])],
    customColumns: ctx.customColumns ?? [],
    hasStatus: !!ctx.hasStatus,
  });
  CATALOGS.set(ctx, { catalog, version: providerVersion });
  return catalog;
}

const asList = (v) => {
  if (Array.isArray(v)) return v;
  if (v == null || v === "") return [];
  if (typeof v === "string")
    return v
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  return [v];
};

// Pass/fail eval cells are stored as "Passed" / "Failed" (api/datasets
// toCellValue) or 1 / 0 / true / false; scores as numbers.
function evalCell(v) {
  if (v === true || v === "Passed" || v === "passed" || v === "pass") return 1;
  if (v === false || v === "Failed" || v === "failed" || v === "fail") return 0;
  return v;
}

function cellOf(row, id) {
  const v = row.values?.[id];
  if (v !== undefined && v !== null) return v;
  const g = row.generated?.[id];
  return g && typeof g === "object" && "value" in g ? g.value : g ?? null;
}

function readRow(row, ctx, field) {
  const rd = field.read;
  switch (rd.k) {
    case "input":
    case "expected":
    case "column": {
      const v = cellOf(row, rd.id);
      return field.type === "eval" ? evalCell(v) : v;
    }
    case "metadata":
      return rd.id ? cellOf(row, rd.id) : row.metadata?.[rd.key] ?? null;
    case "split":
      return (row.split_ids ?? []).map((id) => ctx.splitNames?.[id] ?? id);
    case "tags":
      return asList(row.metadata?.tags ?? row.tags);
    case "row":
      return row.n ?? null;
    case "id":
      return row.id ?? null;
    case "row_status":
      return row.status ?? row.metadata?.status ?? null;
    case "provider":
      return readProvider(rd.name, {
        rowId: row.id,
        datasetId: ctx.datasetId,
        row,
      });
    default:
      return null;
  }
}

// Eval columns compared with a verdict word read their pass / fail.
function readVerdict(row, field) {
  const v = evalCell(cellOf(row, field.read.id));
  const n =
    typeof v === "number"
      ? v
      : typeof v === "string" && v.trim() !== ""
        ? Number(v)
        : NaN;
  if (!Number.isFinite(n)) return null;
  const thr = field.eval?.threshold;
  return thr == null ? null : n >= thr ? "pass" : "fail";
}

/** The evaluation scope of one dataset row. */
export function gridRowScope(row, ctx = {}) {
  return {
    catalog: gridCatalogFor(ctx),
    runIds: [],
    baselineId: null,
    readRow: (field) =>
      field.read?.k === "column_verdict"
        ? readVerdict(row, field)
        : readRow(row, ctx, field),
    readRun: () => null,
  };
}

const COMPILED = new WeakMap();
/** Compile an expression for a grid ctx (cached by text per catalogue). */
export function compileGridExpression(expr, ctx = {}) {
  const catalog = gridCatalogFor(ctx);
  let byText = COMPILED.get(catalog);
  if (!byText) {
    byText = new Map();
    COMPILED.set(catalog, byText);
  }
  const text = String(expr ?? "");
  if (!byText.has(text)) byText.set(text, compileExpression(text, catalog));
  return byText.get(text);
}

/** Does the dataset row match the expression? (false when it does not compile) */
export function gridExpressionMatchesRow(expr, row, ctx = {}) {
  const compiled = compileGridExpression(expr, ctx);
  if (compiled.error) return false;
  return matchesExpression(compiled.ast, gridRowScope(row, ctx));
}

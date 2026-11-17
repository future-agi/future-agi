// the interface contract · what the Data grid door of `Filter by expression` shows, built
// from the dataset's own columns and rows (never fixed text): the grid
// wire filter, the catalogue, value hints with counts, examples, quick
// picks and the count request. Pure. (No Copy as API call: the capability contract a.)
import { compileExpression } from "./expression";
import { gridCatalog, quoteName } from "./fieldCatalog";
import { providerFields } from "./gridScope";
import { quantile } from "./resultsDoorModel";

export const GRID_EXPRESSION_ID = "system:expression";
/** The input's placeholder when the dataset gives no example of its own. */
export const GRID_PLACEHOLDER =
  "Type an expression over this dataset's columns";

/**
 * The input's placeholder: this dataset's first example (built from its own
 * columns and values by gridExamples), so it never names a column the
 * dataset does not have.
 */
export const gridPlaceholder = (examples = []) =>
  examples.find((e) => e?.expr)?.expr ?? GRID_PLACEHOLDER;

/** The grid wire filter (the interface contract §5 A3), built and read like `system:row_ids`. */
export const expressionGridFilter = (expr) => ({
  column_id: GRID_EXPRESSION_ID,
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: String(expr ?? ""),
  },
});

/** The expression a grid filter list carries, or null. */
export const gridExpressionOf = (filters) =>
  (filters ?? []).find((f) => f?.column_id === GRID_EXPRESSION_ID)
    ?.filter_config?.filter_value || null;

/** The list without any expression filter. */
export const withoutGridExpression = (filters) =>
  (filters ?? []).filter((f) => f?.column_id !== GRID_EXPRESSION_ID);

/**
 * Columns for the catalogue from get-dataset-table `column_config` (each
 * carries `role`, `data_type`, `name`, `id`).
 */
export const gridColumnsFromConfig = (config) =>
  (config ?? [])
    .filter((c) => c && c.id && c.name)
    .map((c) => ({
      id: c.id,
      name: c.name,
      role: c.role ?? null,
      data_type: c.data_type,
      deleted: false,
    }));

/** The grid catalogue for these columns plus the registered row fields. */
export const gridCatalogForColumns = (columns) =>
  gridCatalog({ columns: columns ?? [], rowFields: providerFields() });

/** Compile against the client catalogue: the error the dialog shows at once. */
export function gridExpressionError(expr, catalog) {
  const compiled = compileExpression(String(expr ?? ""), catalog);
  return compiled.error ? compiled.error.message : null;
}

const cellText = (row, id) => {
  const v = row?.[id]?.cell_value;
  if (v == null) return null;
  return typeof v === "string" ? v : JSON.stringify(v);
};

/** Value counts per column name over the loaded wire rows (most common first). */
function valueCounts(rows, column) {
  const counts = new Map();
  (rows ?? []).forEach((r) => {
    const t = cellText(r, column.id);
    if (t == null || t === "") return;
    counts.set(t, (counts.get(t) ?? 0) + 1);
  });
  return [...counts]
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
    .map(([value, count]) => ({ value, count }));
}

const isCategorical = (c) =>
  c.role === "metadata" ||
  (c.role === "human" && c.data_type !== "float") ||
  (c.data_type === "text" && c.role !== "input" && c.role !== "expected");

/** Autocomplete values with counts: metadata and other short text columns. */
export function gridValueHints(columns = [], rows = []) {
  const values = {};
  columns.filter(isCategorical).forEach((c) => {
    const counts = valueCounts(rows, c);
    // A column whose every value is distinct (ids, free text) gives no hint.
    if (!counts.length || counts.length === rows.length) return;
    values[c.name] = counts;
    if (c.role === "metadata") values[`metadata.${c.name}`] = counts;
  });
  return { values };
}

/** Median text length of a column over the rows (null when nothing is text). */
export function medianLength(rows, column) {
  const lens = (rows ?? [])
    .map((r) => cellText(r, column.id))
    .filter((t) => t != null && t !== "")
    .map((t) => t.length);
  const m = quantile(lens, 0.5);
  return m == null ? null : Math.round(m);
}

const quoted = (v) => JSON.stringify(String(v));

/**
 * Examples from this dataset (the interface contract §5 A3): two values of a metadata
 * column, the longest text input above its median length, a pass/fail eval
 * that fails, an empty expected.
 */
export function gridExamples(columns = [], rows = []) {
  const out = [];
  const meta = columns
    .filter((c) => c.role === "metadata")
    .map((c) => ({ c, counts: valueCounts(rows, c) }))
    .find(({ counts }) => counts.length >= 2 && counts.length < rows.length);
  if (meta)
    out.push({
      label: `${meta.counts[0].value} or ${meta.counts[1].value} ${meta.c.name}`,
      expr: `metadata.${quoteName(meta.c.name)} in (${quoted(meta.counts[0].value)}, ${quoted(meta.counts[1].value)})`,
    });
  const texts = columns
    .filter((c) => c.role === "input" && c.data_type === "text")
    .map((c) => ({ c, median: medianLength(rows, c) }))
    .filter((x) => x.median != null)
    .sort((a, b) => b.median - a.median);
  if (texts.length)
    out.push({
      label: `${texts[0].c.name} longer than its median`,
      expr: `len(${quoteName(texts[0].c.name)}) > ${texts[0].median}`,
    });
  const ev = columns.find(
    (c) => c.role === "eval" && c.data_type === "boolean",
  );
  if (ev)
    out.push({
      label: `Rows that fail ${ev.name}`,
      expr: `${quoteName(ev.name)} = fail`,
    });
  const expected = columns.find((c) => c.role === "expected");
  if (expected && rows.some((r) => !cellText(r, expected.id)))
    out.push({
      label: `Rows with no ${expected.name}`,
      expr: `${quoteName(expected.name)} is empty`,
    });
  return out;
}

/** Quick picks: rows missing their expected value; rows that fail any pass/fail eval. */
export function gridQuickPicks(columns = []) {
  const out = [];
  const expected = columns.find((c) => c.role === "expected");
  if (expected)
    out.push({
      label: `Hide rows with no ${expected.name}`,
      expr: `not (${quoteName(expected.name)} is empty)`,
    });
  const evals = columns.filter(
    (c) => c.role === "eval" && c.data_type === "boolean",
  );
  if (evals.length >= 2)
    out.push({
      label: "Rows that fail any pass/fail eval",
      expr: evals.map((c) => `${quoteName(c.name)} = fail`).join(" or "),
    });
  return out;
}

/** get-dataset-table params for a filter list (as selectionRows sends them). */
export function gridTableParams(filters, search, { pageSize = 1 } = {}) {
  return {
    current_page_index: 0,
    page_size: pageSize,
    filters: JSON.stringify(filters ?? []),
    sort: JSON.stringify([]),
    ...(search
      ? {
          search: JSON.stringify({
            key: search,
            type: ["text", "image", "audio"],
          }),
        }
      : {}),
  };
}

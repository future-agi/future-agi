// HO D7 (W5 A-H7) · the Expression column type in Add column (W3-10): the
// dialog's open state and the request / response shapes it uses. The value
// of every cell is computed by the one expression evaluator on the server
// (MOCK dynamicColumns.dynamicValue("expression")); this module only maps.
import { create } from "zustand";
import { isPrototypeMode } from "src/utils/prototype";
import {
  CELL_ERROR_PREFIX,
  expressionColumnReads,
} from "src/sections/common/filters/expressionColumn";

// The type's value in Add column's Dynamic list.
export const EXPRESSION_COLUMN_TYPE = "expressionColumn";

// POST add_expression_column/ is a DSX prototype path (integration registry): the
// type is offered only where it is served. An option stays absent until real.
export const expressionColumnOffered = (served = isPrototypeMode()) =>
  Boolean(served);

export const EXPRESSION_COLUMN_OPTION = Object.freeze({
  label: "Expression",
  icon: "mdi:function-variant",
  value: EXPRESSION_COLUMN_TYPE,
  color: "text.primary",
  helpingText:
    'Compute a column from the row\'s other columns with the filter language, e.g. len(expected_reply) or language = "es".',
  keywords: ["expression", "formula", "computed", "derived", "len"],
});

/** `{open: null | {datasetId?, editId?}}`: Add column opens it; Configure edits. */
export const useExpressionColumnStore = create((set) => ({
  open: null,
  openFor: (value) => set(() => ({ open: value })),
  close: () => set(() => ({ open: null })),
}));

/**
 * The create / test / edit body, the same keys in the same order the server
 * stores as the column's config: an edit that keeps the name and the
 * expression keeps the config, so `Only rows whose inputs changed` re-runs
 * only rows whose read columns changed (`reads`: the column ids it reads).
 */
export const expressionBody = ({ name, expr }, columns = []) => ({
  new_column_name: name,
  expr,
  reads: expressionColumnReads(expr, columns),
});

/**
 * `Create column` / `Save and re-run`: the create body, and for an edit the
 * column it edits and the rows it re-runs (`all` | `changed` | `failed`).
 */
export const expressionEditBody = ({ name, expr }, columns, editId, scope) => ({
  ...expressionBody({ name, expr }, columns),
  ...(editId ? { column_id: editId, scope } : {}),
});

/** dry_run preview → the form's `[{row, value, status, error}]` cells. */
export function previewCells(result) {
  return (result?.preview_results ?? []).map((p) => ({
    row: p.row_number,
    status: p.status === "error" ? "error" : "pass",
    error: p.error ?? null,
    value:
      p.status === "error"
        ? null
        : typeof p.output === "string" &&
            p.output.startsWith(`Row ${p.row_number} · ${CELL_ERROR_PREFIX}`)
          ? null
          : p.output,
  }));
}

/** operation-config metadata → the form's initial name and expression. */
export const initialOf = (metadata) => ({
  name: String(metadata?.new_column_name ?? metadata?.name ?? ""),
  expr: String(metadata?.expr ?? ""),
});

/** The server's 400 text (`{result}` envelope) or a fallback. */
export const errorText = (e, fallback) =>
  (typeof e?.result === "string" && e.result) ||
  (typeof e?.response?.data?.result === "string" && e.response.data.result) ||
  (typeof e?.message === "string" && e.message) ||
  fallback;

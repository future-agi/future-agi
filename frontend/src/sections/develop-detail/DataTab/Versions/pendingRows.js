// Pending new rows: change-set `add_row` ops shown as table rows before a
// version is saved. One shape for the grid (DevelopDataV2) and the Row view
// (useRowNavigation), so the two never drift. Pure; no store access.

/** Temp ids of pending rows (`new-1`, `new-lq3x9`); saved rows are uuids. */
export const PENDING_ROW_PREFIX = "new-";
/** IT-R3 G5 P1: rows an import added (`imp-1`, `imp-lq3x92`), unsaved too. */
export const IMPORTED_ROW_PREFIX = "imp-";

/**
 * True for a pending row's temp id (whether or not its op still exists):
 * a typed new row (`new-…`) or an imported one (`imp-…`). Neither exists
 * on the server until a version saves it, so nothing fetches it by id.
 */
export const isPendingRowId = (id) =>
  typeof id === "string" &&
  (id.startsWith(PENDING_ROW_PREFIX) || id.startsWith(IMPORTED_ROW_PREFIX));

/** True for a row an import added and no version has saved yet. */
export const isImportedRowId = (id) =>
  typeof id === "string" && id.startsWith(IMPORTED_ROW_PREFIX);

/** The `add_row` op behind a temp id, or null. */
export const findAddRowOp = (ops, rowId) =>
  (ops ?? []).find((o) => o.op === "add_row" && o.temp_id === rowId) ?? null;

/**
 * One `add_row` op as a table row with a `New` chip (`__pending: "new"`).
 * Cells carry the typed values; `order` is null (no row number yet).
 */
export function pendingRowFromOp(op) {
  return {
    row_id: op.temp_id,
    order: null,
    __pending: "new",
    // IT-R3 G5 P1: an imported row keeps what the import knew (its kind,
    // the session / trace it came from, what failed in production), so
    // the Row view renders it before a version saves it.
    ...(op.kind ? { kind: op.kind } : {}),
    ...(op.expected_source ? { expected_source: op.expected_source } : {}),
    row_meta: {
      split_ids: op.split_ids ?? [],
      source: op.source ?? { type: "manual" },
      metadata: op.metadata ?? {},
      ...(op.production ? { production: op.production } : {}),
    },
    ...Object.fromEntries(
      Object.entries(op.cells ?? {}).map(([col, value]) => [
        col,
        { cell_value: value, status: "pass" },
      ]),
    ),
  };
}

/** Every pending new row in op order. */
export const pendingRowsOf = (ops) =>
  (ops ?? []).filter((o) => o.op === "add_row").map(pendingRowFromOp);

/** True when the op has no typed value yet (a fresh `Add row`). */
export const isBlankAddRow = (op) =>
  !Object.values(op?.cells ?? {}).some((v) => v != null && v !== "");

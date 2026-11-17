import { describe, expect, it } from "vitest";
import {
  findAddRowOp,
  isBlankAddRow,
  isPendingRowId,
  pendingRowFromOp,
  pendingRowsOf,
} from "../pendingRows";

const ADD = {
  op: "add_row",
  temp_id: "new-lq3x9",
  cells: { col_msg: "Where is my order?" },
  split_ids: ["s-hard"],
  metadata: { ticket: "T-9" },
};

describe("pendingRows", () => {
  it("tells temp ids from saved uuids", () => {
    expect(isPendingRowId("new-1")).toBe(true);
    expect(isPendingRowId("new-lq3x9")).toBe(true);
    expect(isPendingRowId("b1000000-0000-4000-8000-000000000003")).toBe(false);
    expect(isPendingRowId(null)).toBe(false);
    expect(isPendingRowId(undefined)).toBe(false);
  });

  it("finds the add_row op of a temp id", () => {
    const ops = [
      { op: "update_cell", row_id: "r-1", column_id: "c", value: "x" },
      ADD,
    ];
    expect(findAddRowOp(ops, "new-lq3x9")).toBe(ADD);
    expect(findAddRowOp(ops, "new-2")).toBeNull();
    expect(findAddRowOp(undefined, "new-2")).toBeNull();
  });

  it("builds the grid / Row view row from the op", () => {
    expect(pendingRowFromOp(ADD)).toEqual({
      row_id: "new-lq3x9",
      order: null,
      __pending: "new",
      row_meta: {
        split_ids: ["s-hard"],
        source: { type: "manual" },
        metadata: { ticket: "T-9" },
      },
      col_msg: { cell_value: "Where is my order?", status: "pass" },
    });
    const blank = pendingRowFromOp({ op: "add_row", temp_id: "new-2" });
    expect(blank.row_meta).toEqual({
      split_ids: [],
      source: { type: "manual" },
      metadata: {},
    });
  });

  it("lists every pending row in op order", () => {
    const ops = [
      ADD,
      { op: "delete_rows", row_ids: ["r-1"] },
      { op: "add_row", temp_id: "new-2", cells: {} },
    ];
    expect(pendingRowsOf(ops).map((r) => r.row_id)).toEqual([
      "new-lq3x9",
      "new-2",
    ]);
    expect(pendingRowsOf(undefined)).toEqual([]);
  });

  it("knows a blank row from a typed one", () => {
    expect(isBlankAddRow({ op: "add_row", cells: {} })).toBe(true);
    expect(isBlankAddRow({ op: "add_row", cells: { a: "" } })).toBe(true);
    expect(isBlankAddRow(ADD)).toBe(false);
  });
});

// IT-R3 G5 P1: imported rows are pending rows that keep what the import knew.
import {
  isImportedRowId as r3IsImportedRowId,
  isPendingRowId as r3IsPendingRowId,
  pendingRowFromOp as r3PendingRowFromOp,
} from "../pendingRows";

describe("IT-R3 G5 P1 imported pending rows", () => {
  it("treats imp- ids as pending (never fetched by id)", () => {
    expect(r3IsPendingRowId("imp-1")).toBe(true);
    expect(r3IsPendingRowId("imp-mvedn7g7-1")).toBe(true);
    expect(r3IsImportedRowId("imp-1")).toBe(true);
    expect(r3IsImportedRowId("new-1")).toBe(false);
  });

  it("keeps the source, kind and production record of an import", () => {
    const row = r3PendingRowFromOp({
      op: "add_row",
      temp_id: "imp-3",
      kind: "conversation",
      source: { kind: "session", session_id: "s-1058" },
      expected_source: "empty",
      production: { outcome: "fail", criterion: "Resolution" },
      cells: { c1: "hi" },
    });
    expect(row.kind).toBe("conversation");
    expect(row.row_meta.source).toEqual({
      kind: "session",
      session_id: "s-1058",
    });
    expect(row.row_meta.production.criterion).toBe("Resolution");
  });
});

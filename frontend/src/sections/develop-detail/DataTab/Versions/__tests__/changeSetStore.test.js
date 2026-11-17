import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  METADATA_COLUMN,
  applyOp,
  cellIndex,
  countOps,
  revertOp,
  toOperations,
  useChangeSet,
  useChangeSetStore,
} from "../changeSetStore";

const DS = "ds-1";
const edit = (row, col, value, before) => ({
  op: "update_cell",
  row_id: row,
  column_id: col,
  value,
  before,
});

describe("applyOp", () => {
  it("keeps one edit per cell and the first `before`", () => {
    let ops = applyOp([], edit("ROW_241", "C_03", "a", ""));
    ops = applyOp(ops, edit("ROW_241", "C_03", "b", "a"));
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ value: "b", before: "" });
  });

  it("drops an edit typed back to its original value", () => {
    let ops = applyOp([], edit("ROW_1", "C_03", "x", "orig"));
    ops = applyOp(ops, edit("ROW_1", "C_03", "orig"));
    expect(ops).toEqual([]);
    expect(applyOp([], edit("ROW_1", "C_03", "same", "same"))).toEqual([]);
  });

  it("folds edits of a pending new row into that row", () => {
    let ops = applyOp([], { op: "add_row", temp_id: "new-1", cells: {} });
    ops = applyOp(ops, edit("new-1", "C_01", "Where is my parcel?"));
    ops = applyOp(ops, {
      op: "set_splits",
      row_ids: ["new-1"],
      add: ["SPLIT_SMOKE"],
      remove: [],
    });
    expect(ops).toHaveLength(1);
    expect(ops[0].cells).toEqual({ C_01: "Where is my parcel?" });
    expect(ops[0].split_ids).toEqual(["SPLIT_SMOKE"]);
  });

  it("nets split changes per row", () => {
    let ops = applyOp([], {
      op: "set_splits",
      row_ids: ["ROW_241", "ROW_12"],
      add: ["SPLIT_HARD"],
      remove: [],
    });
    expect(countOps(ops).split_changes).toBe(2);
    ops = applyOp(ops, {
      op: "set_splits",
      row_ids: ["ROW_241"],
      add: [],
      remove: ["SPLIT_HARD"],
    });
    expect(countOps(ops).split_changes).toBe(1);
    expect(ops[0].row_ids).toEqual(["ROW_12"]);
  });

  it("deletes rows, dropping their pending edits and new rows", () => {
    let ops = applyOp([], edit("ROW_200", "C_03", "x", "y"));
    ops = applyOp(ops, { op: "add_row", temp_id: "new-1" });
    ops = applyOp(ops, { op: "delete_rows", row_ids: ["ROW_200", "new-1"] });
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ op: "delete_rows", row_ids: ["ROW_200"] });
    ops = applyOp(ops, { op: "delete_rows", row_ids: ["ROW_201", "ROW_200"] });
    expect(ops).toHaveLength(1);
    expect(ops[0].row_ids).toEqual(["ROW_200", "ROW_201"]);
    expect(countOps(ops).deleted).toBe(2);
  });

  it("counts a selection delete as unknown", () => {
    const ops = applyOp([], {
      op: "delete_rows",
      selection: { selected_all_rows: true, exclude_row_ids: [], filters: [] },
    });
    expect(countOps(ops).deleted).toBeNull();
  });

  it("ignores unknown op types", () => {
    expect(applyOp([], { op: "rename_dataset" })).toEqual([]);
  });
});

describe("revert, index and operations", () => {
  it("reverts by op id, by cell and by row", () => {
    let ops = applyOp([], edit("ROW_1", "C_01", "a", "b"));
    ops = applyOp(ops, edit("ROW_1", "C_02", "c", "d"));
    ops = applyOp(ops, {
      op: "set_row_metadata",
      row_id: "ROW_1",
      metadata: { reviewed: true },
      before: {},
    });
    ops = applyOp(ops, {
      op: "set_splits",
      row_ids: ["ROW_1"],
      add: ["SPLIT_HARD"],
    });
    expect(revertOp(ops, ops[0].id)).toHaveLength(3);
    expect(revertOp(ops, "ROW_1", "C_02")).toHaveLength(3);
    expect(revertOp(ops, "ROW_1", METADATA_COLUMN)).toHaveLength(3);
    expect(revertOp(ops, "ROW_1")).toEqual([]);
  });

  it("indexes cells with before/after", () => {
    const ops = applyOp([], edit("ROW_40", "C_03", "new", "old"));
    expect(cellIndex(ops)).toEqual({
      ROW_40: { C_03: { before: "old", after: "new", opId: ops[0].id } },
    });
  });

  it("builds the N2 operations body without local fields", () => {
    const ops = applyOp([], edit("ROW_241", "C_03", "Sorry nobody called", ""));
    expect(toOperations(ops)).toEqual([
      {
        op: "update_cell",
        row_id: "ROW_241",
        column_id: "C_03",
        value: "Sorry nobody called",
      },
    ]);
  });
});

describe("useChangeSet", () => {
  beforeEach(() => useChangeSetStore.setState({ sets: {} }));

  it("keeps one change-set per dataset and exposes counts", () => {
    const { result } = renderHook(() => useChangeSet(DS));
    const other = renderHook(() => useChangeSet("ds-2"));
    expect(result.current.hasPending).toBe(false);
    act(() => result.current.addOp(edit("ROW_241", "C_03", "x", "")));
    act(() =>
      result.current.addOp({
        op: "set_splits",
        row_ids: ["ROW_241"],
        add: ["SPLIT_HARD"],
      }),
    );
    expect(result.current.counts).toMatchObject({
      edited: 1,
      split_changes: 1,
      added: 0,
    });
    expect(result.current.byCell.ROW_241.C_03.after).toBe("x");
    expect(other.result.current.hasPending).toBe(false);
    act(() => result.current.revert("ROW_241", "C_03"));
    expect(result.current.counts.edited).toBe(0);
    act(() => result.current.discard());
    expect(result.current.hasPending).toBe(false);
  });

  it("tracks the base version and new/deleted rows", () => {
    const { result } = renderHook(() => useChangeSet(DS));
    act(() => result.current.setBaseVersion("VER_6"));
    act(() => result.current.addOp({ op: "add_row", temp_id: "new-1" }));
    act(() => result.current.addOp({ op: "delete_rows", row_ids: ["ROW_31"] }));
    expect(result.current.baseVersionId).toBe("VER_6");
    expect(result.current.newRowIds).toEqual(["new-1"]);
    expect(result.current.deletedRowIds).toEqual(["ROW_31"]);
  });
});

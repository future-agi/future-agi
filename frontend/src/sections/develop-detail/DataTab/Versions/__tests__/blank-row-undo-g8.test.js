// v2.1 g8 (rows:add-blank): Add row puts one pending blank row; the toast's
// Undo (delete_rows of its temp id) drops it, leaving no pending change.
// Duplicate's copies (selectionCopies) undo the same way.
import { beforeEach, describe, expect, it } from "vitest";
import { useChangeSetStore } from "../changeSetStore";
import { duplicateOps } from "../selectionCopies";

const DS = "ds-blank-undo";
const ops = () => useChangeSetStore.getState().getOps(DS);

describe("pending rows undo", () => {
  beforeEach(() => useChangeSetStore.getState().discard?.(DS));

  it("a blank row and its Undo leave nothing pending", () => {
    const s = useChangeSetStore.getState();
    s.addOp(DS, {
      op: "add_row",
      temp_id: "new-abc",
      cells: {},
      split_ids: [],
    });
    expect(ops().filter((o) => o.op === "add_row")).toHaveLength(1);
    s.addOp(DS, { op: "delete_rows", row_ids: ["new-abc"] });
    expect(ops()).toEqual([]);
  });

  it("Undo of a Duplicate drops exactly its copies", () => {
    const s = useChangeSetStore.getState();
    s.addOp(DS, {
      op: "add_row",
      temp_id: "new-keep",
      cells: {},
      split_ids: [],
    });
    const copies = duplicateOps(
      [
        { op: "add_row", cells: { c: "a" }, split_ids: [] },
        { op: "add_row", cells: { c: "b" }, split_ids: [] },
      ],
      "t-",
    );
    copies.forEach((o) => s.addOp(DS, o));
    expect(ops().filter((o) => o.op === "add_row")).toHaveLength(3);
    s.addOp(DS, { op: "delete_rows", row_ids: copies.map((o) => o.temp_id) });
    expect(ops().map((o) => o.temp_id)).toEqual(["new-keep"]);
  });
});

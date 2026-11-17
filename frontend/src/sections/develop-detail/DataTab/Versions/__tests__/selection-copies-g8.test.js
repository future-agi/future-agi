// v2.1 g8 (grid:selection-actions): Duplicate is a pending change; Add to
// dataset names the columns that did not match; no "Datapoint added".
import { describe, expect, it } from "vitest";
import {
  duplicateOps,
  duplicateToast,
  mergeToast,
  newDatasetToast,
} from "../selectionCopies";

describe("selection copies", () => {
  it("duplicate operations become pending add_row ops with their own temp ids", () => {
    const ops = duplicateOps(
      [
        {
          op: "add_row",
          cells: { c1: "a" },
          split_ids: ["s1"],
          source: { type: "copy", row_id: "r1" },
        },
        { op: "add_row", cells: { c1: "a" }, split_ids: ["s1"] },
      ],
      "x-",
    );
    expect(ops.map((o) => o.temp_id)).toEqual(["new-dup-x-1", "new-dup-x-2"]);
    expect(ops[0]).toMatchObject({
      op: "add_row",
      cells: { c1: "a" },
      split_ids: ["s1"],
      source: { row_id: "r1" },
    });
    expect(ops[0].cells).not.toBe(ops[1].cells);
  });

  it("toasts say what happened", () => {
    expect(duplicateToast({ rows_copied: 2, rows_added: 6 })).toBe(
      "Duplicated 2 rows ×3 · unsaved until you save a version",
    );
    expect(
      mergeToast({
        rows_added: 2,
        target_name: "Billing set",
        version_number: 5,
        unmatched_columns: ["topic"],
        empty_columns: ["tier"],
      }),
    ).toBe(
      "Added 2 rows to Billing set (saved as v5) · not copied (no column of that name): topic · left empty: tier",
    );
    expect(mergeToast({ rows_added: 1, target_name: "B" })).toBe(
      "Added 1 row to B",
    );
    expect(newDatasetToast(2, "B14 two rows")).toBe(
      "Created B14 two rows with 2 rows",
    );
    expect(newDatasetToast(0, "X")).not.toMatch(/Datapoint/);
  });
});

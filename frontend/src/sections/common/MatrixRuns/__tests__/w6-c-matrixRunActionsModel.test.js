// W6 C4: the Matrix's run-again actions: a column starts the next run of its
// config (named by the one label rule), a cell and a row amend the runs that
// own them, and a later run reads "vs #2C" from the Matrix compare model.
import { describe, expect, it } from "vitest";
import {
  cellRunItem,
  columnRunItem,
  parentOf,
  rowRunItem,
  runToast,
  vsParentLine,
} from "../matrixRunActionsModel";

const run = (letter, extra = {}) => ({
  run_id: `r-${letter}`,
  label: `#2${letter}`,
  experiment_id: "exp-2",
  output_column_id: `col-${letter}`,
  target_type: "prompt",
  ...extra,
});
const row = { row_id: "row-33", label: "Row 33" };

describe("columnRunItem", () => {
  it("names the new run and its rows; the parent keeps its results", () => {
    const it2 = columnRunItem(run("C"), { rows: 30 });
    expect(it2).toMatchObject({
      label: "Run #2C again",
      next: "#2C·2",
      secondary: "Creates #2C·2 on 30 rows; #2C keeps its results",
      body: { source_ids: ["r-C"] },
      experimentId: "exp-2",
    });
    expect(
      columnRunItem(run("C", { attempts_count: 3 }), { rows: 1, noun: "task" }),
    ).toMatchObject({
      next: "#2C·4",
      secondary: "Creates #2C·4 on 1 task; #2C keeps its results",
    });
  });
  it("is absent for a run Future AGI cannot start again itself", () => {
    expect(
      columnRunItem(run("U", { target_type: "external" }), { rows: 30 }),
    ).toBeNull();
    expect(columnRunItem(null)).toBeNull();
  });
});

describe("cellRunItem / rowRunItem", () => {
  it("a cell amends one cell of its run", () => {
    expect(cellRunItem(run("B"), row)).toMatchObject({
      label: "Run this cell again",
      experimentId: "exp-2",
      body: { cells: [{ column_id: "col-B", row_id: "row-33" }] },
    });
    expect(cellRunItem(run("B", { output_column_id: null }), row)).toBeNull();
  });
  it("a row amends each runnable run at that row, one request per experiment", () => {
    const item = rowRunItem(
      [
        run("A"),
        run("B"),
        run("X", { experiment_id: "exp-3" }),
        run("U", { target_type: "external" }),
      ],
      row,
    );
    expect(item.label).toBe("Run Row 33 on every run");
    expect(item.secondary).toBe(
      "3 runs (#2A, #2B, #2X) · each adds a result to its History",
    );
    expect(item.requests).toEqual([
      {
        experimentId: "exp-2",
        body: {
          cells: [
            { column_id: "col-A", row_id: "row-33" },
            { column_id: "col-B", row_id: "row-33" },
          ],
        },
      },
      {
        experimentId: "exp-3",
        body: { cells: [{ column_id: "col-X", row_id: "row-33" }] },
      },
    ]);
    expect(rowRunItem([], row)).toBeNull();
  });
});

describe("vs the run it started from", () => {
  const parent = run("C");
  const child = run("C", {
    run_id: "r-C2",
    label: "#2C·2",
    attempt: 2,
    parent_run_id: "r-C",
  });
  it("finds the parent of a later run only", () => {
    expect(parentOf(child, { "r-C": parent })).toBe(parent);
    expect(parentOf(parent, { "r-C": parent })).toBeNull();
  });
  it("counts rows better and worse by pass rate", () => {
    const matrix = {
      columns: { "r-C": { trials: 1 }, "r-C2": { trials: 1 } },
      rows: [
        {
          row_id: "1",
          cells: { "r-C": { trials: [0] }, "r-C2": { trials: [1] } },
        },
        {
          row_id: "2",
          cells: { "r-C": { trials: [0] }, "r-C2": { trials: [1] } },
        },
        {
          row_id: "3",
          cells: { "r-C": { trials: [1] }, "r-C2": { trials: [0] } },
        },
        {
          row_id: "4",
          cells: { "r-C": { trials: [1] }, "r-C2": { trials: [1] } },
        },
      ],
    };
    expect(vsParentLine(matrix, child, parent)).toBe(
      "vs #2C: 2 better · 1 worse",
    );
    const same = { ...matrix, rows: [matrix.rows[3]] };
    expect(vsParentLine(same, child, parent)).toBe("vs #2C: same on every row");
    expect(vsParentLine(null, child, parent)).toBeNull();
  });
});

describe("runToast", () => {
  it("reads the started labels or the amendment", () => {
    expect(runToast({ data: { result: { labels: ["#2C·2"] } } }, "x")).toBe(
      "Started #2C·2.",
    );
    expect(
      runToast(
        { data: { result: { amended: { summary: "1 cell re-run" } } } },
        "x",
      ),
    ).toBe("1 cell re-run");
    expect(runToast({}, "fallback")).toBe("fallback");
  });
});

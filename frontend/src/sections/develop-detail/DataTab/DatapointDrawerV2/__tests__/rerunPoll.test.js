// options-truth L3 G7 W7: Re-run this cell keeps reading the row until the
// cell (and the evals that read it) stop running.
import { describe, expect, it } from "vitest";
import { cellsStillRunning } from "../useRowActions";

const q = (cells) => [[["k"], { current: { row_id: "r1", ...cells } }]];

describe("cellsStillRunning", () => {
  it("is true while any re-run column reads running", () => {
    expect(
      cellsStillRunning(q({ c1: { status: "running" } }), "r1", ["c1"]),
    ).toBe(true);
    expect(
      cellsStillRunning(
        q({ c1: { status: "pass" }, e1: { status: "running" } }),
        "r1",
        ["c1", "e1"],
      ),
    ).toBe(true);
  });
  it("is false once every column has its value", () => {
    expect(
      cellsStillRunning(q({ c1: { status: "pass", cell_value: "hi" } }), "r1", [
        "c1",
      ]),
    ).toBe(false);
  });
  it("keeps polling while the row is not loaded yet", () => {
    expect(cellsStillRunning([], "r1", ["c1"])).toBe(true);
  });
});

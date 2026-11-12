// options-truth L3 G9 (fix 7): a matrix with pending tries refetches, so a
// rerun's verdict and Failed counts arrive when it completes.
import { describe, expect, it } from "vitest";
import { matrixPending } from "../r2";

const cell = (trials, ids, status = []) => ({
  trials,
  trace_ids: ids,
  trace_summary: status.map((s) => ({ status: s })),
});
const result = (...cells) => ({
  rows: cells.map((c, i) => ({ row_id: `r${i}`, cells: { run: c } })),
});

describe("matrixPending", () => {
  it("is pending while a try is queued or running", () => {
    expect(matrixPending(result(cell([1, null], ["t1", null])))).toBe(true);
    expect(matrixPending(result(cell([null], ["t1"], ["running"])))).toBe(true);
  });
  it("is settled when every try has an outcome or finished unscored", () => {
    expect(matrixPending(result(cell([1, 0, "error"], ["a", "b", "c"])))).toBe(
      false,
    );
    expect(matrixPending(result(cell([null], ["t1"], ["completed"])))).toBe(
      false,
    );
    expect(matrixPending(undefined)).toBe(false);
    expect(matrixPending({ rows: [] })).toBe(false);
  });
});

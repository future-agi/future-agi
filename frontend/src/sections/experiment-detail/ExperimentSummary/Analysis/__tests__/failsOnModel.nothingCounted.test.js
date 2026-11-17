// The Summary's Fails on cards: a run with nothing to count (no evaluation
// with a Pass/fail result) fails no task there, so it shows no `0/2` and no
// count of failing tasks.
import { describe, expect, it } from "vitest";
import { failsOnModel } from "../failsOnModel";

const row = (id, n, category, cells) => ({
  row_id: id,
  n,
  label: `T${n}`,
  group_values: { category },
  cells,
});
// `a` errored on every try; `b` failed one try of each row.
const matrixOf = (columns) => ({
  rows: [
    row("r1", 1, "refund", {
      a: { trials: ["error", "error"] },
      b: { trials: [true, false] },
    }),
    row("r2", 2, "refund", {
      a: { trials: ["error", "error"] },
      b: { trials: [true, false] },
    }),
  ],
  ...(columns ? { columns } : {}),
});
const COUNTS = {
  a: { trials: 2, pass_fail_evals: 0 },
  b: { trials: 2, pass_fail_evals: 1 },
};

describe("Fails on for a run with nothing to count", () => {
  it("fails no task", () => {
    const [a] = failsOnModel(matrixOf(COUNTS), { runIds: ["a", "b"] });
    expect(a).toMatchObject({
      runId: "a",
      failing: 0,
      total: 2,
      categories: [],
      tasks: [],
      failingIds: [],
    });
  });

  it("still lists the tasks a run with a figure fails", () => {
    // Green at the base.
    const [, b] = failsOnModel(matrixOf(COUNTS), { runIds: ["a", "b"] });
    expect(b).toMatchObject({ failing: 2, total: 2, failingIds: ["r1", "r2"] });
    expect(b.tasks[0]).toMatchObject({ id: "r1", passed: 1, tries: 2 });
  });

  it("keeps today's count when the matrix does not say", () => {
    // Green at the base: with no count of Pass/fail evaluations the errored
    // tries are in the count, as before.
    const [a] = failsOnModel(matrixOf(null), { runIds: ["a", "b"] });
    expect(a).toMatchObject({ failing: 2, failingIds: ["r1", "r2"] });
  });
});

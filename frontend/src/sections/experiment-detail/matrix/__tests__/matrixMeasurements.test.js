import { describe, expect, it } from "vitest";
import { cellNumbers, columnStats } from "../matrixModel";

const matrixOf = (cell, passFailEvals) => ({
  columns: {
    run: { trials: cell.trials.length, pass_fail_evals: passFailEvals },
  },
  rows: [{ row_id: "row-measured", cells: { run: cell } }],
});

const completed = {
  trials: [null],
  trace_summary: [{ status: "completed", trial_number: 1 }],
  cost_usd: [0.25],
  wall_ms: [1200],
};

describe("recorded Matrix measurements without a verdict", () => {
  it("retains measurements without inventing Success", () => {
    expect(cellNumbers(completed)).toEqual({
      pass: 0,
      scored: 0,
      total: 1,
      rate: null,
      cost: 0.25,
      costSum: 0.25,
      wall: 1200,
      allPass: false,
      noPass: false,
    });
    const stats = columnStats(matrixOf(completed, 0), "run", null);
    expect([stats.costPerRow, stats.p50]).toEqual([0.25, 1200]);
    expect([stats.costText, stats.p50Text]).toEqual(["$0.25", "1.2 s"]);
    expect(stats.passAt1).toEqual({ pass: 0, n: 0, rate: null });
    expect([stats.passAt1Text, stats.passHatKText]).toEqual(["—", "—"]);
    expect(stats.noPassFail).toBe(true);
    expect([stats.better, stats.worse]).toEqual([0, 0]);
  });

  it("averages completed measurements independently of verdicts", () => {
    const mixed = {
      trials: [true, null],
      trace_summary: [
        { status: "completed", trial_number: 1 },
        { status: "completed", trial_number: 2 },
      ],
      cost_usd: [0.25, 0.75],
      wall_ms: [1200, 2800],
    };
    expect(cellNumbers(mixed)).toEqual({
      pass: 1,
      scored: 1,
      total: 2,
      rate: 1,
      cost: 0.5,
      costSum: 1,
      wall: 2000,
      allPass: false,
      noPass: false,
    });
    const stats = columnStats(matrixOf(mixed, 1), "run", null);
    expect([stats.costPerRow, stats.p50]).toEqual([0.5, 2000]);
    expect(stats.passAt1).toEqual({ pass: 1, n: 1, rate: 1 });
    expect(stats.passHatK).toEqual({ pass: 0, n: 0, rate: null });
  });

  it.each(["queued", "running", "stopped", "not_reached", "infra"])(
    "excludes measurements for a %s try",
    (status) => {
      const cell = {
        trials: [null, status === "infra" ? "infra" : null],
        trace_summary: [{ status: "completed" }, { status }],
        cost_usd: [0.25, 100],
        wall_ms: [1200, 5000],
      };
      const numbers = cellNumbers(cell);
      expect([numbers.cost, numbers.costSum, numbers.wall]).toEqual([
        0.25, 0.25, 1200,
      ]);
      expect([numbers.pass, numbers.scored, numbers.rate]).toEqual([
        0,
        0,
        null,
      ]);
      const stats = columnStats(matrixOf(cell, 0), "run", null);
      expect([stats.costPerRow, stats.p50]).toEqual([0.25, 1200]);
      expect(stats.passAt1).toEqual({ pass: 0, n: 0, rate: null });
    },
  );

  it("keeps zeros and ignores absent or nonfinite measurements", () => {
    const cell = {
      trials: [null, null, null, null],
      trace_summary: Array.from({ length: 4 }, () => ({
        status: "completed",
      })),
      cost_usd: [0, null, Infinity, NaN],
      wall_ms: [0, undefined, NaN, Infinity],
    };
    const numbers = cellNumbers(cell);
    expect([numbers.cost, numbers.costSum, numbers.wall]).toEqual([0, 0, 0]);
    const stats = columnStats(matrixOf(cell, 0), "run", null);
    expect([stats.costPerRow, stats.p50]).toEqual([0, 0]);
    expect(stats.passAt1).toEqual({ pass: 0, n: 0, rate: null });
  });
});

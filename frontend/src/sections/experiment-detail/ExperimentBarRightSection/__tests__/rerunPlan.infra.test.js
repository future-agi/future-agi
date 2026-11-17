import { describe, expect, it } from "vitest";
import { failedItemText, rerunPlan } from "../rerunPlan";

const RUNS = [
  { run_id: "ra", label: "#2A", concurrency: 2 },
  { run_id: "rb", label: "#2B", concurrency: 1 },
  { run_id: "rc", label: "#2C", concurrency: 1 },
];

const cell = (trials, amount = 0.125, ms = 60000) => ({
  trials,
  cost_usd: trials.map(() => amount),
  wall_ms: trials.map(() => ms),
});

describe("rerunPlan infrastructure failures", () => {
  it("enables an infrastructure-only rerun with one count per row", () => {
    const plan = rerunPlan(
      {
        rows: [
          { row_id: "failed", cells: { ra: cell(["infra", "infra"]) } },
          { row_id: "passed", cells: { ra: cell([1, true]) } },
        ],
      },
      [RUNS[0]],
    );

    expect(plan.failed.rows).toBe(0);
    expect(plan.failed.errored).toBe(1);
    expect(plan.failed.runs.map((r) => r.run)).toEqual([RUNS[0]]);
    expect(plan.failed.cost).toBe(0.25);
    expect(plan.failed.ms).toBe(60000);
    expect(plan.failed.estimate).toEqual({
      rows: 1,
      trials: null,
      tries: 2,
      cost: 0.25,
      ms: 60000,
      runnerPaid: false,
      someRunnerPaid: false,
    });
    expect(plan.all.cost).toBe(0.5);
    expect(plan.all.ms).toBe(120000);
    expect(failedItemText(plan, "task")).toEqual({
      enabled: true,
      primary: "Failed tasks (1)",
      secondary: "1 task · 2 tries · ~$0.25 · ~1 min · 1 errored",
    });
  });

  it("includes ordinary-error and infrastructure-only runs but not passing runs", () => {
    const plan = rerunPlan(
      {
        rows: [
          {
            row_id: "shared",
            cells: {
              ra: cell(["error", "infra"]),
              rb: cell(["infra", "infra"], 0.25, 120000),
              rc: cell([1, true]),
            },
          },
        ],
      },
      RUNS,
    );

    expect(
      plan.perRun.map(({ id, failed, errored }) => ({ id, failed, errored })),
    ).toEqual([
      { id: "ra", failed: 0, errored: 1 },
      { id: "rb", failed: 0, errored: 1 },
      { id: "rc", failed: 0, errored: 0 },
    ]);
    expect(plan.failed.rows).toBe(0);
    expect(plan.failed.errored).toBe(2);
    expect(plan.failed.runs.map((r) => r.run)).toEqual([RUNS[0], RUNS[1]]);
    expect(plan.failed.runs.map((r) => r.run.run_id)).toEqual(["ra", "rb"]);
    expect(plan.failed.cost).toBe(0.75);
    expect(plan.failed.ms).toBe(240000);
    expect(plan.failed.estimate).toEqual({
      rows: 2,
      trials: null,
      tries: 4,
      cost: 0.75,
      ms: 240000,
      runnerPaid: false,
      someRunnerPaid: false,
    });
    expect(failedItemText(plan, "task")).toEqual({
      enabled: true,
      primary: "Failed tasks (2)",
      secondary:
        "2 tasks · 4 tries · ~$0.75 · ~4 min · #2A 1 · #2B 1 · 2 errored",
    });
  });

  it.each([0, false])(
    "counts a row with check failure %s and infrastructure errors only once",
    (failed) => {
      const plan = rerunPlan(
        {
          rows: [
            {
              row_id: "mixed",
              cells: { ra: [failed, "infra", "error", "infra"] },
            },
          ],
        },
        [RUNS[0]],
      );

      expect(plan.failed.rows).toBe(1);
      expect(plan.failed.errored).toBe(0);
      expect(plan.failed.runs.map((r) => r.run)).toEqual([RUNS[0]]);
      expect(plan.failed.estimate).toEqual({
        rows: 1,
        trials: null,
        tries: 4,
        cost: null,
        ms: null,
        runnerPaid: false,
        someRunnerPaid: false,
      });
      expect(failedItemText(plan, "task")).toEqual({
        enabled: true,
        primary: "Failed tasks (1)",
        secondary: "1 task · 4 tries",
      });
    },
  );
});

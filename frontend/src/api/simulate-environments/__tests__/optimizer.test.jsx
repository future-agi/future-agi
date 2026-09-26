import { describe, it, expect } from "vitest";
import { mapOptimizationRuns } from "../optimizer";

describe("mapOptimizationRuns", () => {
  it("maps the real runs-list table into rows with a human optimiser label", () => {
    const raw = {
      result: {
        table: [
          {
            id: "opt-1",
            optimisation_name: "Refund fix v1",
            started_at: "2026-09-16T09:00:00.000Z",
            no_of_trials: 8,
            optimiser_type: "protegi",
            status: "completed",
          },
          {
            id: "opt-2",
            optimisation_name: "Escalation tuning",
            optimiser_type: "bayesian",
            status: "running",
          },
        ],
        metadata: { total_rows: 2 },
      },
    };

    const rows = mapOptimizationRuns(raw);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      id: "opt-1",
      name: "Refund fix v1",
      trials: 8,
      optimiserLabel: "ProTeGi",
      status: "completed",
    });
    expect(rows[1].optimiserLabel).toBe("Bayesian Search");
    expect(rows[1].trials).toBe(0);
  });

  it("returns an empty list when the payload has no table", () => {
    expect(mapOptimizationRuns(null)).toEqual([]);
    expect(mapOptimizationRuns({ result: {} })).toEqual([]);
  });
});

import { describe, it, expect } from "vitest";
import {
  mapOptimizerAnalysis,
  mapOptimizationRuns,
} from "../optimizer";

// A realistic `optimiser-analysis` payload — the object the endpoint wraps in
// `res.data.result`. Shapes mirror the product's FixMyAgent consumer:
// response.{agent,domain,system}_level.actionable_recommendations[].
const ANALYSIS = {
  status: "completed",
  last_updated: "2026-09-15T10:00:00.000Z",
  response: {
    insights: "The agent skips the refund-eligibility check under time pressure.",
    agent_level: {
      actionable_recommendations: [
        {
          heading: "Confirm eligibility before refunding",
          recommendation: "Add an explicit eligibility gate to the refund tool.",
          breaking_points: ["Refunded an out-of-window order", "No policy citation"],
          priority: "high",
          call_execution_ids: ["c1", "c2", "c3"],
          branch_category: "Refunds",
        },
        {
          heading: "Acknowledge the customer's name",
          recommendation: "Greet by name when the CRM provides it.",
          breaking_points: [],
          priority: "low",
          call_execution_ids: ["c4"],
          branch_category: "Unknown",
        },
      ],
    },
    domain_level: {
      actionable_recommendations: [
        {
          heading: "Escalate angry customers",
          recommendation: "Route to a human after two negative sentiment turns.",
          breaking_points: ["Missed an escalation"],
          priority: "medium",
          call_execution_ids: ["c5", "c6"],
        },
      ],
    },
    system_level: {
      human_comparison_summary: "Humans resolved these with a policy lookup the agent lacks.",
      actionable_recommendations: [
        {
          heading: "Policy tool is missing",
          recommendation: "The environment exposes no policy lookup — add one.",
          breaking_points: [],
          priority: "high",
          call_execution_ids: [],
        },
      ],
    },
  },
};

describe("mapOptimizerAnalysis", () => {
  it("maps the real payload into the diagnosis view-model", () => {
    const vm = mapOptimizerAnalysis(ANALYSIS);

    expect(vm.status).toBe("completed");
    expect(vm.isWorking).toBe(false);
    expect(vm.hasResponse).toBe(true);
    expect(vm.summary).toMatch(/eligibility check/);
    expect(vm.humanComparison).toMatch(/policy lookup/);
    expect(vm.lastUpdated).toBe("2026-09-15T10:00:00.000Z");
  });

  it("merges agent + domain into fixable, ranked by priority then reach", () => {
    const vm = mapOptimizerAnalysis(ANALYSIS);

    // agent(high, low) + domain(medium) → high, medium, low.
    expect(vm.fixable.map((r) => r.heading)).toEqual([
      "Confirm eligibility before refunding",
      "Escalate angry customers",
      "Acknowledge the customer's name",
    ]);
    expect(vm.fixable[0].callsAffected).toBe(3);
    expect(vm.fixable[0].branchCategory).toBe("Refunds");
    // "Unknown" branch collapses to null.
    expect(vm.fixable[2].branchCategory).toBeNull();
  });

  it("keeps system-level findings separate as environmental", () => {
    const vm = mapOptimizerAnalysis(ANALYSIS);
    expect(vm.environmental).toHaveLength(1);
    expect(vm.environmental[0].heading).toBe("Policy tool is missing");
    // system findings never leak into fixable.
    expect(vm.fixable.some((r) => r.level === "system")).toBe(false);
  });

  it("flags a still-running analysis and an empty diagnosis", () => {
    const running = mapOptimizerAnalysis({ status: "running", response: null });
    expect(running.isWorking).toBe(true);
    expect(running.hasResponse).toBe(false);
    expect(running.fixable).toEqual([]);

    const empty = mapOptimizerAnalysis(null);
    expect(empty.hasResponse).toBe(false);
    expect(empty.fixable).toEqual([]);
    expect(empty.environmental).toEqual([]);
  });
});

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

// W1-9 B5 (CP-08): per-step compare rows read both aggregates exactly.
import { describe, expect, it } from "vitest";
import { aggregatePaths } from "src/sections/common/Trajectory/pathModel";
import {
  compareSteps,
  failingText,
  nodeMetrics,
  stepCompareText,
  triesThrough,
} from "../stepCompare";

const v = (name, ms, tokens, cost, failing) => ({
  name,
  type: name === "reply" ? "llm" : "tool",
  ms,
  tokens,
  cost,
  ...(failing ? { failing_checks: failing } : {}),
});
const agg = (attempts) =>
  aggregatePaths({ runId: "r", nodeSource: "milestones", attempts });

// baseline: lookup_order fails order_found on 2 of 3 tries
const BASE = agg([
  {
    rowId: "a",
    trial: 1,
    failed: true,
    visits: [
      v("lookup_order", 1200, 0, 0, ["order_found"]),
      v("reply", 900, 1000, 0.002),
    ],
  },
  {
    rowId: "b",
    trial: 1,
    failed: true,
    visits: [
      v("lookup_order", 1000, 0, 0, ["order_found"]),
      v("reply", 1100, 1200, 0.003),
    ],
  },
  {
    rowId: "c",
    trial: 1,
    failed: false,
    visits: [v("lookup_order", 800, 0, 0), v("reply", 1000, 800, 0.001)],
  },
]);
// candidate: lookup_order passes; a new issue_refund step fails once
const CAND = agg([
  {
    rowId: "a",
    trial: 1,
    failed: false,
    visits: [v("lookup_order", 600, 0, 0), v("reply", 900, 900, 0.002)],
  },
  {
    rowId: "b",
    trial: 1,
    failed: true,
    visits: [
      v("lookup_order", 700, 0, 0),
      v("issue_refund", 300, 0, 0, ["refund_amount"]),
      v("reply", 800, 700, 0.001),
    ],
    end_failing: ["Task success"],
  },
  {
    rowId: "c",
    trial: 1,
    failed: false,
    visits: [v("lookup_order", 500, 0, 0), v("reply", 1000, 800, 0.001)],
  },
]);

describe("compareSteps", () => {
  it("node metrics are the aggregate's own numbers", () => {
    const n = BASE.graph.nodes.find((x) => x.id === "lookup_order");
    expect(nodeMetrics(n)).toEqual({
      visits: 3,
      tries: 3,
      passShare: 1 / 3,
      meanMs: 1000,
      meanTokens: 0,
      meanCost: 0,
      failing: { order_found: 2 },
    });
    expect(failingText(n)).toBe("✕ order_found 2");
  });

  it("one row per step, baseline vs candidate, biggest change first", () => {
    const rows = compareSteps(BASE, CAND);
    expect(rows.map((r) => r.id)).toEqual([
      "issue_refund",
      "lookup_order",
      "reply",
      "__end__",
    ]);
    const [refund, lookup, reply, end] = rows;
    expect(refund.only).toBe("cand");
    expect(lookup.delta).toEqual({
      visits: 0,
      passPts: 33.3,
      ms: -400,
      tokens: 0,
      cost: 0,
      checks: -2,
    });
    expect(lookup.checks).toEqual([
      { name: "order_found", base: 2, cand: 0, delta: -2 },
    ]);
    expect(reply.delta.passPts).toBe(33.3);
    expect(end.checks).toEqual([
      { name: "Task success", base: 0, cand: 1, delta: 1 },
    ]);
    expect(stepCompareText(lookup)).toBe(
      "lookup_order · 3 → 3 visits · pass 33% → 67% · 1.0 s → 600 ms · 0 → 0 tok · $0.00 → $0.00 · ✕ order_found 2 → 0",
    );
  });

  it("a step's tries are the example rows of the paths through it, failing first", () => {
    expect(triesThrough([CAND, BASE], "issue_refund")).toEqual(["b"]);
    expect(triesThrough([BASE], "lookup_order")).toEqual(["a", "b", "c"]);
  });

  it("paths carry per-step failing checks (path table chips)", () => {
    const p = CAND.paths.find((x) =>
      x.steps.some((s) => s.name === "issue_refund"),
    );
    expect(p.steps.map((s) => s.failing_checks ?? null)).toEqual([
      null,
      { refund_amount: 1 },
      null,
    ]);
    expect(
      CAND.graph.nodes.find((n) => n.id === "__end__").failing_checks,
    ).toEqual({
      "Task success": 1,
    });
  });
});

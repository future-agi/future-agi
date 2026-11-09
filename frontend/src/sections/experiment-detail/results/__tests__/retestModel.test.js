import { describe, expect, it } from "vitest";
import {
  criterionMetric,
  failingTurnOf,
  productionCellText,
  retestSummary,
} from "../retestModel";

const prod = (turn = 5) => ({
  outcome: "fail",
  criterion: "Resolution",
  reason: "Answered with self-help.",
  failing_turn: turn,
  reply: "Here is our guide.",
});
const conversation = JSON.stringify({
  turns: [
    { role: "customer", content: "Locked out." },
    { role: "agent", content: "Clear your cache." },
    { role: "customer", content: "Not a cache issue." },
    { role: "agent", content: "Here is a guide." },
    { role: "customer", content: "Escalate this now." },
    { role: "agent", content: "Anything else?" },
    { role: "customer", content: "Hello?" },
    { role: "agent", content: "Bye." },
  ],
});
const metrics = [
  {
    key: "eval:resolution",
    name: "Resolution",
    kind: "eval",
    output_type: "pass_fail",
  },
];
const rowOf = (id, value, extra = {}) => ({
  example_id: id,
  inputs: { conversation },
  production: prod(),
  results: { R: { scores: { "eval:resolution": { value } } } },
  ...extra,
});

describe("re-test of production failures (iterate r2 G3 P1)", () => {
  it("answers does it still fail", () => {
    const rows = [
      rowOf("a", 1),
      rowOf("b", 1),
      rowOf("c", 1),
      rowOf("d", 1),
      rowOf("e", 0),
      rowOf("f", 0),
      { example_id: "g", results: {} },
    ];
    const s = retestSummary(rows, { runId: "R", metrics });
    expect(s.text).toBe(
      "Re-test of 6 production failures: 4 now pass · 2 still fail (Resolution)",
    );
    expect(s.stillIds).toEqual(["e", "f"]);
    expect(
      retestSummary([{ example_id: "x" }], { runId: "R", metrics }),
    ).toBeNull();
  });
  it("the Production cell and the failing turn", () => {
    expect(productionCellText(prod())).toBe("✗ Failed · Resolution · turn 5");
    const t = failingTurnOf(rowOf("a", 1));
    expect(t).toMatchObject({ total: 8, n: 5, customer: "Escalate this now." });
    // An agent turn: the customer message it answered.
    expect(failingTurnOf(rowOf("a", 1, { production: prod(6) })).customer).toBe(
      "Escalate this now.",
    );
    expect(criterionMetric(metrics, "Resolution").key).toBe("eval:resolution");
  });
});

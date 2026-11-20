import { describe, expect, it } from "vitest";
import { wrongWayExamplesOf } from "../wrongWay";

const rule = { text: "Refund only to the original card." };
const goals = [{ text: "Refund the right amount", mark: "critical" }];
const gate = (ok) => [{ id: "vacuous", ok }];

describe("wrongWayExamplesOf", () => {
  it("names a rule and a tool example, both caught", () => {
    const { examples, none } = wrongWayExamplesOf(
      { provedAt: 1 },
      { rule, tools: ["issue_refund"], goals, gates: gate(true) },
    );
    expect(none).toBeNull();
    expect(examples.map((e) => e.name)).toEqual([
      "Ignores the rule",
      "Calls issue_refund on the wrong record",
    ]);
    expect(examples.every((e) => e.statusLabel === "Caught")).toBe(true);
  });

  it("marks the last example not caught when the gate failed", () => {
    const { examples } = wrongWayExamplesOf(
      { provedAt: 1 },
      { rule, tools: ["issue_refund"], goals, gates: gate(false) },
    );
    expect(examples.map((e) => e.statusLabel)).toEqual([
      "Caught",
      "Not caught",
    ]);
  });

  it("says Not run before the gate ran", () => {
    const { examples } = wrongWayExamplesOf({}, { rule, gates: gate(null) });
    expect(examples[0].statusLabel).toBe("Not run");
  });

  it("explains an empty list for AI-judge-only and legacy cases", () => {
    expect(wrongWayExamplesOf({ judgedOnly: true }).none).toMatch(/AI judge/);
    expect(wrongWayExamplesOf({}, { state: { id: "kept" } }).none).toMatch(
      /before proofs existed/,
    );
  });

  it("uses a row's own negative controls as they are", () => {
    const { examples } = wrongWayExamplesOf({
      negativeControls: [{ key: "x", name: "Refunds twice", caught: false }],
    });
    expect(examples[0]).toMatchObject({
      name: "Refunds twice",
      statusLabel: "Not caught",
    });
  });
});

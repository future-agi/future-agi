import { describe, expect, it } from "vitest";
import { placeFailures } from "../participantsSubGoals";

/* A call: caller asks (0–10 s), Front desk hands to Refunds (2–6 s), which
   calls issue_refund (3 s); then the caller pushes back (20–30 s). */
const seq = {
  lanes: [
    { id: "user", name: "Caller" },
    { id: "desk", name: "Front desk" },
    { id: "refunds", name: "Refunds" },
  ],
  steps: [
    {
      n: 1,
      kind: "message",
      from: "user",
      to: "desk",
      at: 0,
      answer: { kind: "answer", at: 10 },
    },
    {
      n: 2,
      kind: "handoff",
      from: "desk",
      to: "refunds",
      at: 2,
      answer: { kind: "answer", at: 6 },
    },
    {
      n: 3,
      kind: "tool",
      from: "refunds",
      tool: "issue_refund",
      at: 3,
      bad: true,
      answer: null,
    },
    {
      n: 4,
      kind: "message",
      from: "user",
      to: "desk",
      at: 20,
      answer: { kind: "answer", at: 30 },
    },
  ],
};

describe("placeFailures (plan: which component caused it; MA-2: never guess)", () => {
  it("a judged moment goes on the coordinator's reply in that turn", () => {
    const [f] = placeFailures(seq, [{ text: "Holds the line", at: 25 }]);
    expect(f).toMatchObject({ n: 4, row: "answer", lane: "desk" });
  });

  it("a Call <tool> check goes on that tool call, on the lane that ran it", () => {
    const [f] = placeFailures(seq, [
      { text: "Call issue_refund once", at: null },
    ]);
    expect(f).toMatchObject({ n: 3, row: "tool", lane: "refunds" });
  });

  it("a tool that was never called is not tied to a step", () => {
    const [f] = placeFailures(seq, [
      { text: "Call escalate_to_human", at: null },
    ]);
    expect(f.n).toBeNull();
    expect(f.untied).toMatch(/never called/);
  });

  it("no evidence, no step: Unknown instead of a guess", () => {
    const [f] = placeFailures(seq, [{ text: "Explains the policy", at: null }]);
    expect(f.n).toBeNull();
    expect(f.untied).toMatch(/didn't point/);
  });

  it("a moment far from every reply stays untied", () => {
    const [f] = placeFailures(seq, [{ text: "Holds the line", at: 15 }]);
    expect(f.n).toBeNull();
  });
});

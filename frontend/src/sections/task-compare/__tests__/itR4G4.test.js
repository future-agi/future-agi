// IT-R4 G4 P4: side by side says what the other run did differently, and
// which tries it shows and why.
import { describe, expect, it } from "vitest";
import { differentlyLine } from "../storyGrid";
import { trialCaption } from "../workspaceModel";

const steps = (n) =>
  Array.from({ length: n }, (_, i) => ({ n: i + 1, kind: "tool" }));
const base = {
  id: "a",
  label: "Support Agent v3",
  verdict: { scored: 3, pass: 0, key: "fail" },
  steps: steps(7),
  trial: { trial: 1, cost_usd: 0.012 },
  trials: [
    { trial: 1, outcome: "fail" },
    { trial: 2, outcome: "fail" },
    { trial: 3, outcome: "fail" },
  ],
};
const cand = {
  id: "b",
  label: "Claude Code",
  verdict: { scored: 3, pass: 3, key: "pass" },
  steps: steps(16),
  trial: { trial: 2, cost_usd: 0.2 },
  trials: [
    { trial: 1, outcome: "pass", cost_usd: 0.1 },
    { trial: 2, outcome: "pass", cost_usd: 0.2 },
    { trial: 3, outcome: "pass", cost_usd: 0.3 },
  ],
};
const grid = {
  items: [
    {
      type: "row",
      cells: [
        { beats: [{ text: 'Read the order id → "8830"', kind: "think" }] },
        { beats: [] },
      ],
    },
    {
      type: "fold",
      lane: 1,
      rows: [
        {
          cells: [
            { beats: [] },
            { beats: [{ text: "Told the customer: hi", kind: "reply" }] },
          ],
        },
        {
          cells: [
            { beats: [] },
            {
              beats: [
                {
                  text: 'Searched the help center for "damaged on arrival refund or replacement"',
                  kind: "retrieval",
                },
              ],
            },
          ],
        },
      ],
    },
  ],
  firstWrongBeat: null,
};

describe("differentlyLine", () => {
  it("names the other run's first own action, the step counts and the cost ratio", () => {
    expect(differentlyLine({ grid, lanes: [base, cand] })).toBe(
      'Claude Code took 16 steps (Support Agent v3: 7): it searched the help center for "damaged on arrival ref…"; the 9 extra steps cost 17×.',
    );
  });
  it("says nothing with one lane", () => {
    expect(differentlyLine({ grid, lanes: [base] })).toBeNull();
  });
});

describe("trialCaption", () => {
  it("says which tries are shown and why", () => {
    expect(trialCaption({ lanes: [base, cand] })).toBe(
      "Trial 1 of 3 vs trial 2 of 3: Support Agent v3's first failed try and Claude Code's typical passing try (median cost).",
    );
  });
  it("says a picked try is the user's", () => {
    expect(
      trialCaption({
        lanes: [base, { ...cand, trial: { trial: 3 } }],
        picked: { b: 3 },
      }),
    ).toMatch(/Claude Code's try you picked/);
  });
});

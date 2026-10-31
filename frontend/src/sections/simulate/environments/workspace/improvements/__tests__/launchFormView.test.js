import { describe, it, expect } from "vitest";

import {
  heldBackCount,
  heldBackLine,
  countsLine,
  reviewSentence,
  budgetSteps,
  minuteSteps,
  launchError,
  scenarioStatus,
} from "../launchFormView";

const LIMITS = {
  budget_credits_max: 50000,
  max_minutes_max: 1440,
  scenarios_max: 200,
};
const SURFACES = [
  { id: "prompt", label: "Instructions" },
  { id: "tools", label: "Tools" },
  { id: "settings", label: "Settings" },
];

describe("held back", () => {
  it("holds back a third from 12 picked", () => {
    expect(heldBackCount(11)).toBe(0);
    expect(heldBackCount(12)).toBe(4);
    expect(heldBackCount(25)).toBe(8);
  });

  it("says how many, or that there are too few", () => {
    expect(heldBackLine(12)).toBe("About 4 held back to prove the fix");
    expect(heldBackLine(11)).toBe(
      "Too few failing cases to hold some back. The fix will be proven on the picked cases only.",
    );
  });

  it("counts what is fixed, held back and protected", () => {
    expect(countsLine(12, 9)).toBe(
      "8 failing to fix · 4 held back to prove it · 9 must keep passing",
    );
    expect(countsLine(3, 1)).toBe("3 failing to fix · 1 must keep passing");
  });
});

describe("review sentence", () => {
  it("reads the request back in one sentence", () => {
    expect(
      reviewSentence({
        targets: 1,
        protectedCount: 9,
        surfaces: ["prompt"],
        all: SURFACES,
        budget: 1500,
        minutes: 150,
      }),
    ).toBe(
      "Fix 1 failing case without breaking 9 protected cases, changing instructions only, up to 1,500 credits or 2 h 30 min.",
    );
    expect(
      reviewSentence({
        targets: 8,
        protectedCount: 1,
        surfaces: ["prompt", "tools", "settings"],
        all: SURFACES,
        budget: 25000,
        minutes: 60,
      }),
    ).toBe(
      "Fix 8 failing cases without breaking 1 protected case, changing instructions, tools and settings only, up to 25,000 credits or 1 h.",
    );
  });
});

describe("budget and time choices", () => {
  it("stay within the limits and include the default", () => {
    expect(budgetSteps(1500, { ...LIMITS, budget_credits_max: 5000 })).toEqual([
      500, 1000, 1500, 2500, 5000,
    ]);
    expect(budgetSteps(1800, LIMITS)).toContain(1800);
    expect(Math.max(...budgetSteps(1500, LIMITS))).toBe(50000);
    expect(minuteSteps(150, { ...LIMITS, max_minutes_max: 120 })).toEqual([
      15, 30, 60, 120,
    ]);
    expect(Math.max(...minuteSteps(150, LIMITS))).toBe(1440);
  });
});

describe("launch checks", () => {
  const ok = {
    targets: ["a"],
    protectedKeys: ["b"],
    surfaces: ["prompt"],
    limits: LIMITS,
  };
  it("passes a valid pick", () => {
    expect(launchError(ok)).toBeNull();
  });
  it.each([
    [{ ...ok, targets: [] }, "Pick at least one failing case to fix."],
    [
      { ...ok, targets: Array.from({ length: 201 }, (_, i) => `t${i}`) },
      "Pick at most 200 cases to fix.",
    ],
    [
      { ...ok, protectedKeys: ["a"] },
      "A case can't be both fixed and protected.",
    ],
    [{ ...ok, surfaces: [] }, "Pick at least one part it may change."],
  ])("refuses %#", (input, message) => {
    expect(launchError(input)).toBe(message);
  });
});

describe("scenario status", () => {
  it("names passed, failed and unknown", () => {
    expect(scenarioStatus(true)).toBe("Passing");
    expect(scenarioStatus(false)).toBe("Failing");
    expect(scenarioStatus(null)).toBe("Not measured");
  });
});

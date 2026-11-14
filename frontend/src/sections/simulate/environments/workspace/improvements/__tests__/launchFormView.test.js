import { describe, it, expect } from "vitest";

import {
  heldBackCount,
  countsLine,
  reviewSentence,
  budgetSteps,
  minuteSteps,
  launchError,
  variationsNote,
  blockedReason,
} from "../launchFormView";

const LIMITS = {
  budget_credits_max: 50000,
  max_minutes_max: 1440,
  scenarios_min: 1,
  cases_min: 1,
  variations_per_case: 3,
  scenarios_max: 200,
};
const FLOOR = {
  ...LIMITS,
  scenarios_min: 3,
  cases_min: 12,
  verified_cases_min: 6,
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

  it("counts what is fixed, held back and protected", () => {
    expect(countsLine(12, 9)).toBe(
      "12 failing: 8 to fix, 4 held back to prove it · 9 must keep passing",
    );
    expect(countsLine(3, 1)).toBe("3 failing to fix · 1 must keep passing");
  });
});

describe("variations", () => {
  it("writes variations first below the floor, instead of refusing", () => {
    expect(countsLine(5, 1, FLOOR)).toBe(
      "5 failing, and up to 15 variations of them · 1 must keep passing",
    );
    expect(variationsNote(7, FLOOR)).toBe(
      "An epoch needs 12 failing cases. It first writes 3 variations of each, then fixes the ones the current version also fails.",
    );
    expect(variationsNote(12, FLOOR)).toBeNull();
    expect(
      launchError({
        targets: ["a", "b", "c", "d", "e"],
        protectedKeys: [],
        surfaces: ["prompt"],
        limits: FLOOR,
      }),
    ).toBeNull();
  });

  it("says a fix from too few cases can only end with Limited validation", () => {
    const tail =
      "With fewer than 6 failing cases it can only end with Limited validation";
    expect(variationsNote(5, FLOOR, "archive")).toMatch(
      new RegExp(`${tail}\\.$`),
    );
    expect(variationsNote(5, FLOOR, "github")).toMatch(
      new RegExp(`${tail} and opens a draft pull request\\.$`),
    );
    expect(variationsNote(6, FLOOR, "github")).not.toMatch(
      /Limited validation/,
    );
  });

  it("blocks Start for the variations only when the pick needs them", () => {
    const options = {
      blocked: null,
      variations_blocked: {
        code: "fix_suite_draft_open",
        message: "Save first",
      },
      limits: FLOOR,
    };
    expect(blockedReason(options, 5)).toBe("Save first");
    expect(blockedReason(options, 12)).toBeNull();
    expect(blockedReason({ ...options, blocked: { message: "Busy" } }, 5)).toBe(
      "Busy",
    );
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
  it.each([
    [ok, null],
    [{ ...ok, targets: [] }, "Pick at least one failing case to fix."],
    [
      { ...ok, limits: FLOOR },
      "Needs at least 3 failing cases: with 3 variations of each, they can reach the 12 an epoch needs.",
    ],
    [
      { ...ok, targets: Array.from({ length: 201 }, (_, i) => `t${i}`) },
      "Pick at most 200 cases to fix.",
    ],
    [
      { ...ok, protectedKeys: ["a"] },
      "A case can't be both fixed and protected.",
    ],
    [{ ...ok, surfaces: [] }, "Pick at least one part it may change."],
  ])("passes a valid pick and refuses the rest %#", (input, message) => {
    expect(launchError(input)).toBe(message);
  });
});

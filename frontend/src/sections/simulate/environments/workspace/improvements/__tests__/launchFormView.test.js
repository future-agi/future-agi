import { describe, it, expect } from "vitest";

import {
  countsLine,
  reviewSentence,
  budgetSteps,
  minuteSteps,
  launchError,
  variationsNote,
  blockerOf,
  costLimitOf,
  isTraced,
  seesModels,
  seenModelsOf,
  tracesUnreadable,
  marginSteps,
} from "../launchFormView";
import { heldBackOf } from "../epochBlockers";

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
  it("counts what is fixed, held back and protected, as the server holds back", () => {
    expect(countsLine(12, 9, LIMITS, { keys: ["a", "b", "c", "d"] })).toBe(
      "12 failing: 8 to fix, 4 held back to prove it · 9 protected",
    );
    // The server held back fewer than a third: its count, not a guess.
    expect(countsLine(12, 9, LIMITS, { keys: ["a", "b", "c"] })).toBe(
      "12 failing: 9 to fix, 3 held back to prove it · 9 protected",
    );
    expect(countsLine(3, 1, LIMITS, { keys: [] })).toBe(
      "3 failing to fix · 1 protected",
    );
    expect(countsLine(12, 1, LIMITS, { pending: "edited" })).toBe(
      "12 failing to fix · 1 protected",
    );
  });
});

describe("variations", () => {
  it("writes variations first below the floor, instead of refusing", () => {
    expect(countsLine(5, 1, FLOOR)).toBe(
      "5 failing, and up to 15 variations of them · 1 protected",
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

  it("says a fix from too few cases can end with Limited validation at best", () => {
    // Nothing held back ends weaker still: Proven on the targets only.
    const tail =
      "With fewer than 6 failing cases it can end with Limited validation at best";
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
        code: "fix_draft_open",
        message: "Save first",
      },
      limits: FLOOR,
    };
    expect(blockerOf(options, 5)).toEqual(options.variations_blocked);
    expect(blockerOf(options, 12)).toBeNull();
    const busy = { code: "environment_updating", message: "Busy" };
    expect(blockerOf({ ...options, blocked: busy }, 5)).toBe(busy);
  });
});

describe("held back on the form", () => {
  const options = (keys, suggested, casesMin = 12) => ({
    held_back_scenario_keys: keys,
    suggested: { target_scenario_keys: suggested },
    limits: { cases_min: casesMin },
  });
  const twelve = Array.from({ length: 12 }, (_, i) => `c${i}`);

  it("shows the server's cases for the suggested targets", () => {
    expect(heldBackOf(options(["c1"], twelve), twelve)).toEqual({
      keys: ["c1"],
    });
  });

  it("says the launch picks them from targets the person changed", () => {
    const thirteen = [...twelve, "c12"];
    expect(heldBackOf(options(["c1"], thirteen), twelve)).toEqual({
      pending: "edited",
    });
  });

  it("says they come with the variations below the floor, however the targets were set", () => {
    expect(heldBackOf(options(null, twelve.slice(0, 5)), twelve.slice(0, 5))).toEqual({
      pending: "variations",
    });
    // Unticked down below the floor: variations first, not a promise of held-back cases.
    expect(heldBackOf(options(["c1"], twelve), twelve.slice(0, 10))).toEqual({
      pending: "variations",
    });
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
    [{ ...ok, seenModels: ["m1"], models: ["m1"] }, null],
    [{ ...ok, seenModels: ["m1"], models: [] }, "Allow at least one model."],
    [
      { ...ok, seenModels: ["m1", "vertex_ai/m2"], models: ["m1"] },
      "Allow every model this run called: vertex_ai/m2.",
    ],
    [{ ...ok, seenModels: ["m1", "vertex_ai/m2"], models: ["m1", "M2"] }, null],
    // A fix that sees no model needs none allowed.
    [{ ...ok, seenModels: [], models: [] }, null],
    [{ ...ok, costLimit: "0.05" }, null],
    [
      { ...ok, costLimit: "0" },
      "The cost limit must be a number of dollars above 0.",
    ],
    [
      { ...ok, costLimit: "five" },
      "The cost limit must be a number of dollars above 0.",
    ],
  ])("passes a valid pick and refuses the rest %#", (input, message) => {
    expect(launchError(input)).toBe(message);
  });
});

describe("constraints", () => {
  it("reads an empty cost limit as none", () => {
    expect(costLimitOf("")).toBeNull();
    expect(costLimitOf("  ")).toBeNull();
    expect(costLimitOf(" 0.02 ")).toBe(0.02);
    expect(costLimitOf("-1")).toBeNaN();
  });

  it("offers the margins with the default among them", () => {
    expect(marginSteps(25)).toEqual([0, 10, 25, 50, 100]);
    expect(marginSteps(40)).toEqual([0, 10, 25, 40, 50, 100]);
    // No increase is a choice of its own, offered once even when it is the value.
    expect(marginSteps(0)).toEqual([0, 10, 25, 50, 100]);
  });

  it("tells an unreadable run's traces from an untraced agent", () => {
    expect(
      tracesUnreadable({ blocked: { code: "fix_traces_unreadable" } }),
    ).toBe(true);
    expect(tracesUnreadable({ blocked: { code: "fix_run_active" } })).toBe(
      false,
    );
    expect(tracesUnreadable({ blocked: null })).toBe(false);
  });

  it("calls an agent traced when its spans named a model or measured its cost", () => {
    const options = (allowed_models, agent_cost_metered) => ({
      defaults: { allowed_models, agent_cost_metered },
    });
    expect(isTraced(options([], false))).toBe(false);
    expect(isTraced(options([], true))).toBe(true);
    expect(isTraced(options(["m1"], false))).toBe(true);
    expect(seesModels(options([], true))).toBe(false);
    expect(seesModels(options(["m1"], false))).toBe(true);
  });

  it("reads options that don't list the models as a run that saw none, without throwing", () => {
    expect(seesModels({ defaults: {} })).toBe(false);
    expect(seesModels({})).toBe(false);
    expect(seenModelsOf({ defaults: {} })).toEqual([]);
    expect(seenModelsOf({ defaults: { allowed_models: ["m1"] } })).toEqual([
      "m1",
    ]);
  });

  it("says only the minimum when the options leave out how variations fill the gap", () => {
    const older = { scenarios_min: 12, scenarios_max: 200 };
    const input = {
      targets: ["a"],
      protectedKeys: [],
      surfaces: ["prompt"],
      limits: older,
    };
    expect(launchError(input)).toBe("Needs at least 12 failing cases.");
    expect(variationsNote(1, older)).toBeNull();
    expect(countsLine(1, 9, older)).toBe("1 failing to fix · 9 protected");
  });
});

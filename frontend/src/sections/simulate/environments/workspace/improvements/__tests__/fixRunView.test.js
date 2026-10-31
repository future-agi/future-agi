import { describe, it, expect } from "vitest";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import {
  STATE_LABEL,
  OUTCOME_LABEL,
  fixName,
  identityLine,
  winnerOf,
  isShipped,
  pts,
  headline,
  verdictCards,
  checksLine,
  stepRows,
  heldToRows,
  formatMinutes,
  formatCredits,
} from "../fixRunView";

const byId = (id) => FIX_RUN_FIXTURES.find((f) => f.id === id);

describe("labels", () => {
  it("names every contract state", () => {
    expect(STATE_LABEL).toEqual({
      queued: "Queued",
      running: "Running",
      stopping: "Stopping",
      ready: "Ready for review",
      no_improvement: "No safe improvement",
      not_reproduced: "Could not reproduce",
      out_of_budget: "Stopped at budget",
      stopped: "Stopped",
      failed: "Failed",
    });
  });

  it("names every candidate outcome", () => {
    expect(OUTCOME_LABEL).toEqual({
      winner: "Winner",
      finalist: "Passed, not picked",
      rejected: "Rejected",
      pending: "Running",
    });
  });
});

describe("identity", () => {
  it("names the fix by its number", () => {
    expect(fixName(byId("fix-ready-archive"))).toBe("Improvement 10");
    expect(fixName(byId("fix-unknown-identity"))).toBe("Improvement");
  });

  it("builds the agent × environment line", () => {
    expect(identityLine(byId("fix-ready-github"))).toBe(
      "agent v8 · main @ 0487e25 × environment v1 · new env",
    );
    expect(identityLine(byId("fix-ready-archive"))).toBe(
      "agent v8 × environment v1 · new env",
    );
  });

  it("drops unknown parts instead of printing null", () => {
    const line = identityLine(byId("fix-unknown-identity"));
    expect(line).toBe("agent v8 · new env");
    expect(line).not.toMatch(/null|undefined/);
  });
});

describe("winner and shipped", () => {
  it("finds the winner", () => {
    expect(winnerOf(byId("fix-ready-archive")).attempt).toBe(3);
    expect(winnerOf(byId("fix-search"))).toBeUndefined();
  });

  it("is shipped when the winner's version is the environment default", () => {
    const shipped = byId("fix-ready-shipped");
    expect(isShipped(shipped, winnerOf(shipped))).toBe(true);
    const open = byId("fix-ready-archive");
    expect(isShipped(open, winnerOf(open))).toBe(false);
  });
});

describe("headline", () => {
  it("leads with the held-back set when there is one", () => {
    expect(headline(byId("fix-ready-archive"))).toBe(
      "Fixed 2 of 2 failing scenarios on the held-back set, broke 1.",
    );
  });

  it("falls back to the targets when nothing was held back", () => {
    expect(headline(byId("fix-ready-no-held"))).toBe(
      "Fixed 3 of 3 target scenarios, broke none.",
    );
  });

  it("uses the report or failure for other endings", () => {
    expect(headline(byId("fix-no-improvement"))).toBe(
      "No safe improvement: every candidate broke a must-pass scenario.",
    );
    expect(headline(byId("fix-failed"))).toBe(
      "The engine could not start the agent's sandbox.",
    );
    expect(headline(byId("fix-stopped"))).toBeNull();
  });
});

describe("verdict cards", () => {
  it("shows held back, target and overfit gap", () => {
    const cards = verdictCards(byId("fix-ready-archive"));
    expect(cards.map((c) => c.id)).toEqual(["held", "target", "gap"]);
    expect(cards[0]).toMatchObject({
      label: "Held back",
      from: 17,
      to: 83,
      delta: 66,
      note: "re-run after the pick",
    });
    expect(cards[1]).toMatchObject({
      label: "Target",
      from: 33,
      to: 100,
      delta: 67,
      note: "on the scenarios it was tuned on",
    });
    expect(cards[2]).toMatchObject({
      label: "Overfit gap",
      value: 17,
      note: "between target and held back",
    });
  });

  it("hides held back and overfit gap when nothing was held back", () => {
    const cards = verdictCards(byId("fix-ready-no-held"));
    expect(cards.map((c) => c.id)).toEqual(["target"]);
  });

  it("computes point changes", () => {
    expect(pts([0.33, 1.0])).toBe(67);
    expect(pts([0.5, 0.4])).toBe(-10);
  });
});

describe("checks line", () => {
  it("counts passed tiers and leaves skipped ones out", () => {
    const fix = byId("fix-ready-archive");
    expect(checksLine(winnerOf(fix).verdict.tiers)).toBe(
      "Checks · 5 of 5 passed",
    );
    const rejected = fix.candidates.find((c) => c.outcome === "rejected");
    expect(checksLine(rejected.verdict.tiers)).toBe("Checks · 1 of 2 passed");
  });
});

describe("step rows", () => {
  it("marks the current step and fills its lines", () => {
    const rows = stepRows(byId("fix-search"));
    expect(rows.map((r) => [r.id, r.label, r.state])).toEqual([
      ["reproduce", "Reproduce", "done"],
      ["search", "Search", "now"],
      ["verify", "Verify", "todo"],
      ["done", "Ready for review", "todo"],
    ]);
    expect(rows[0].lines).toEqual([
      "Starts from Run 1 on v8",
      "The current version varied by 17 pts between repeats.",
    ]);
    expect(rows[1].lines).toEqual([
      "2 of up to 4 trials · best so far 67% on 6 target scenarios",
      "Starts from its diagnosis: The instructions never ask the agent to escalate cancellations",
    ]);
    expect(rows[2].lines).toEqual([
      "2 held-back scenarios it never saw and 9 must-pass scenarios",
    ]);
    expect(rows[3].lines).toEqual(["You choose the trial to keep and ship"]);
  });

  it("omits lines whose data has not arrived", () => {
    const rows = stepRows(byId("fix-unknown-identity"));
    expect(rows[0].lines).toEqual([]);
    expect(rows[1].lines).toEqual([]);
    expect(rows[2].lines).toEqual([]);
  });

  it("marks every step done once the fix is done", () => {
    expect(
      stepRows(byId("fix-ready-archive")).every((r) => r.state === "done"),
    ).toBe(true);
  });
});

describe("held to", () => {
  it("lists what the search is held to", () => {
    expect(heldToRows(byId("fix-ready-archive"))).toEqual([
      { label: "Allowed changes", value: "Instructions" },
      { label: "Time cap", value: "2 h 30 min" },
      { label: "Budget", value: "1,500 credits" },
      { label: "Protected cases", value: "1 protected" },
      {
        label: "Must-pass",
        value:
          "A case that passed every time before must not drop beyond normal noise. A case that fails every run is run again to confirm before it counts.",
      },
      { label: "Model", value: "unchanged" },
    ]);
  });

  it("adds locked files when there are some", () => {
    const rows = heldToRows(byId("fix-ready-github"));
    expect(rows.find((r) => r.label === "Allowed changes").value).toBe(
      "Instructions, Code",
    );
    expect(rows.find((r) => r.label === "Locked files").value).toBe(
      "spec.json",
    );
  });

  it("formats minutes and credits", () => {
    expect(formatMinutes(150)).toBe("2 h 30 min");
    expect(formatMinutes(60)).toBe("1 h");
    expect(formatMinutes(45)).toBe("45 min");
    expect(formatCredits(25000)).toBe("25,000 credits");
  });
});

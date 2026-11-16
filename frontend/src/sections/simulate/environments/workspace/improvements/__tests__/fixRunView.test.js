import { describe, it, expect } from "vitest";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import {
  checksLine,
  fixName,
  headline,
  heldToRows,
  identityLine,
  isShipped,
  outcomeLabel,
  STATE_LABEL,
  stepRows,
  verdictCards,
  winnerOf,
} from "../fixRunView";

const byId = (id) => FIX_RUN_FIXTURES.find((f) => f.id === id);

describe("labels and identity", () => {
  it("calls a passing trial a finalist while live and Not kept once finished", () => {
    expect(outcomeLabel("finalist", true)).toBe("Finalist");
    expect(outcomeLabel("finalist", false)).toBe("Not kept");
    expect(outcomeLabel("winner", true)).toBe("Winner");
  });

  it("names the fix and its agent × environment, dropping unknown parts", () => {
    expect(fixName(byId("fix-ready-archive"))).toBe("Improvement 10");
    expect(identityLine(byId("fix-ready-github"))).toBe(
      "agent v8 · main @ 0487e25 × environment v1 · new env",
    );
    expect(identityLine(byId("fix-ready-archive"))).toBe(
      "agent v8 × environment v1 · new env",
    );
    expect(fixName(byId("fix-unknown-identity"))).toBe("Improvement");
    expect(identityLine(byId("fix-unknown-identity"))).toBe(
      "agent v8 · new env",
    );
  });

  it("is shipped when the winner's version is the environment default", () => {
    const shipped = byId("fix-ready-shipped");
    expect(isShipped(shipped, winnerOf(shipped))).toBe(true);
    const open = byId("fix-ready-archive");
    expect(isShipped(open, winnerOf(open))).toBe(false);
  });
});

describe("headline", () => {
  it("leads with the held-back set, else the targets", () => {
    expect(headline(byId("fix-ready-archive"))).toBe(
      "Held-back pass rate 17% → 83%, broke 1.",
    );
    expect(headline(byId("fix-ready-no-held"))).toBe(
      "Target pass rate 33% → 100%, broke none.",
    );
  });

  it("uses the report for other endings", () => {
    expect(headline(byId("fix-no-improvement"))).toBe(
      "No safe improvement: every candidate broke a must-pass scenario.",
    );
    expect(headline(byId("fix-stopped"))).toBeNull();
  });

  it("leads a failed fix with why it failed, not the report's summary", () => {
    const fix = {
      ...byId("fix-failed"),
      report: { ending: "failed", summary: "The fix did not finish." },
      failure: {
        code: "engine_exited",
        message: "The engine stopped: the model provider refused the key.",
      },
    };
    expect(headline(fix)).toBe(
      "The engine stopped: the model provider refused the key.",
    );
  });
});

describe("verdict", () => {
  it("shows held back, target and overfit gap", () => {
    const cards = verdictCards(byId("fix-ready-archive"));
    expect(cards.map((c) => c.id)).toEqual(["held", "target", "gap"]);
    expect(cards[0]).toMatchObject({ from: 17, to: 83, delta: 66 });
    expect(cards[1]).toMatchObject({ from: 33, to: 100, delta: 67 });
    expect(cards[2]).toMatchObject({ value: 17 });
  });

  it("shows only the target card when nothing was held back", () => {
    const cards = verdictCards(byId("fix-ready-no-held"));
    expect(cards.map((c) => c.id)).toEqual(["target"]);
  });

  it("counts passed checks and leaves skipped ones out", () => {
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
    expect(rows.map((r) => [r.id, r.state])).toEqual([
      ["reproduce", "done"],
      ["search", "now"],
      ["verify", "todo"],
      ["done", "todo"],
    ]);
    expect(rows[0].lines).toEqual([
      "Starts from Run 1 on v8",
      "The current version varied by 17 pts between repeats.",
    ]);
    expect(rows[1].lines[0]).toBe(
      "2 of up to 4 trials · best so far 67% on 6 target scenarios",
    );
    expect(rows[2].lines).toEqual([
      "2 held-back scenarios it never saw and 9 must-pass scenarios, including the 1 you protected",
    ]);
  });

  it("counts must-pass scenarios alone when none were protected", () => {
    const fix = byId("fix-search");
    const rows = stepRows({ ...fix, plan: { ...fix.plan, protected: 0 } });
    expect(rows[2].lines).toEqual([
      "2 held-back scenarios it never saw and 9 must-pass scenarios",
    ]);
  });

  it("omits lines whose data has not arrived", () => {
    const rows = stepRows(byId("fix-unknown-identity"));
    expect(rows.slice(0, 3).map((r) => r.lines)).toEqual([[], [], []]);
  });

  it("starts with writing variations when the run had too few failing cases", () => {
    const fix = byId("fix-search");
    const pending = {
      ...fix,
      step: "variations",
      plan: null,
      request: {
        ...fix.request,
        target_scenario_keys: ["a", "b", "c", "d"],
        variations_pending: true,
        variation_parents: {},
      },
    };
    const rows = stepRows(pending);
    expect(rows.map((r) => [r.id, r.state])).toEqual([
      ["variations", "now"],
      ["reproduce", "todo"],
      ["search", "todo"],
      ["verify", "todo"],
      ["done", "todo"],
    ]);
    expect(rows[0].lines).toEqual([
      "Writes variations of the 4 failing cases, then keeps those the current version also fails",
    ]);

    const settled = stepRows({
      ...fix,
      request: {
        ...pending.request,
        target_scenario_keys: ["a", "b", "c", "d", "a-v1", "b-v2"],
        variations_pending: false,
        variation_parents: { "a-v1": "a", "b-v2": "b" },
      },
    });
    expect(settled.map((r) => [r.id, r.state]).slice(0, 3)).toEqual([
      ["variations", "done"],
      ["reproduce", "done"],
      ["search", "now"],
    ]);
    expect(settled[0].lines).toEqual([
      "Kept 2 variations the current version failed: 6 failing cases",
    ]);
  });

  it("names the ending of a fix with too few failing cases", () => {
    expect(STATE_LABEL.too_few_cases).toBe("Too few failing cases");
  });

  it("marks every step done once the fix is done", () => {
    expect(
      stepRows(byId("fix-ready-archive")).every((r) => r.state === "done"),
    ).toBe(true);
  });
});

describe("held to", () => {
  it("lists the limits the search is held to, with locked files when there are some", () => {
    const value = (fix, label) =>
      heldToRows(fix).find((r) => r.label === label)?.value;
    const archive = byId("fix-ready-archive");
    expect(value(archive, "Allowed changes")).toBe("Instructions");
    expect(value(archive, "Time cap")).toBe("2 h 30 min");
    expect(value(archive, "Budget")).toBe("1,500 credits");
    expect(value(archive, "Protected cases")).toBe("1 protected");
    expect(value(archive, "Locked files")).toBeUndefined();
    const github = byId("fix-ready-github");
    expect(value(github, "Allowed changes")).toBe("Instructions, Code");
    expect(value(github, "Locked files")).toBe("spec.json");
  });

  it("shows the spend against the budget only while the fix is live", () => {
    const rows = heldToRows(byId("fix-search"));
    const budget = rows.findIndex((r) => r.label === "Budget");
    expect(rows[budget + 1]).toEqual({
      label: "Spent",
      value: "412 of 1,500 credits",
    });
    expect(
      heldToRows(byId("fix-ready-archive")).some((r) => r.label === "Spent"),
    ).toBe(false);
  });
});

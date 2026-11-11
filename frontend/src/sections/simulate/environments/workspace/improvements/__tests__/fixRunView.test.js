import { describe, it, expect } from "vitest";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import {
  checksLine,
  fixName,
  headline,
  heldToRows,
  identityLine,
  isShipped,
  checkIcon,
  checkLabel,
  describeChecks,
  noSafeNumbers,
  outcomeLabel,
  rejectionChecks,
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
    expect(headline(byId("fix-stopped"))).toBeNull();
    const old = { ...byId("fix-no-improvement"), candidates: [] };
    // A no-safe fix whose trials hold no checks falls back to the engine's summary.
    expect(headline(old)).toBe(
      "No safe improvement: every candidate broke a must-pass scenario.",
    );
  });

  it("says in one sentence what stopped the trials of a no-safe fix", () => {
    // Both fixture trials were rejected at the target check.
    expect(headline(byId("fix-no-improvement"))).toBe(
      "No safe improvement: neither trial improved the target cases.",
    );
    const atHeldBack = (attempt) => ({
      attempt,
      outcome: "rejected",
      verdict: {
        tiers: [
          { name: "target", status: "passed", detail: "62% vs 12%" },
          {
            name: "held-back",
            status: "failed",
            detail: "50% pass vs baseline 39% (p = 0.500)",
          },
        ],
      },
    });
    const real = {
      ...byId("fix-no-improvement"),
      candidates: [atHeldBack(1), atHeldBack(2)],
    };
    const said = headline(real);
    expect(said).toBe(
      "No safe improvement: both trials improved the target cases but not the held-back set.",
    );
    // No numbers, p-values, pipes or case keys in the headline itself.
    expect(said).not.toMatch(/\d|p =|\|/);
    expect(noSafeNumbers(real)).toEqual({
      trials: [
        {
          attempt: 1,
          label: "Held-back set",
          detail: "50% pass vs baseline 39% (p = 0.500)",
        },
        {
          attempt: 2,
          label: "Held-back set",
          detail: "50% pass vs baseline 39% (p = 0.500)",
        },
      ],
      engineNote:
        "No safe improvement: every candidate broke a must-pass scenario.",
    });
    const mixed = {
      ...real,
      candidates: [atHeldBack(1), byId("fix-no-improvement").candidates[1]],
    };
    expect(headline(mixed)).toBe(
      "No safe improvement: no trial passed every check (Trial 1 at Held-back set, Trial 2 at Target cases).",
    );
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

  it("says how many checks could not be measured on a verdict that failed none", () => {
    const tiers = [
      { name: "repeats", status: "passed" },
      { name: "ceilings", status: "skipped" },
      { name: "model-lock", status: "skipped" },
    ];
    expect(checksLine(tiers)).toBe(
      "Checks · 1 of 1 passed · 2 couldn't measure",
    );
    // After a failed check the rest were not run, not unverified.
    expect(
      checksLine([{ name: "target", status: "failed" }, ...tiers.slice(1)]),
    ).toBe("Checks · 0 of 1 passed");
  });

  it("names each check in words, never by the engine's id", () => {
    const names = [
      "static",
      "target",
      "held-back",
      "must-pass",
      "repeats",
      "ceilings",
      "model-lock",
    ];
    expect(names.map((n) => checkLabel(n))).toEqual([
      "Static checks",
      "Target cases",
      "Held-back set",
      "Must-pass set",
      "Repeat runs",
      "Cost and latency",
      "Model unchanged",
    ]);
    expect(
      checkLabel("model-lock", { allowed_models: ["model-a", "model-b"] }),
    ).toBe("Approved models");
    // A check this page does not know yet is still said in words.
    expect(checkLabel("new-check_kind")).toBe("New check kind");
  });

  it("gives a check that could not measure its own icon, apart from one not run", () => {
    const unmeasured = checkIcon({ status: "skipped" });
    const notRun = checkIcon({ status: "skipped", notRun: true });
    expect(unmeasured).not.toBe(notRun);
    expect([unmeasured, notRun]).not.toContain(checkIcon({ status: "passed" }));
  });

  it("says which earlier check stopped a check that was not run", () => {
    const tiers = [
      { name: "repeats", status: "passed" },
      { name: "ceilings", status: "skipped" },
      { name: "model-lock", status: "failed" },
    ];
    // Skipped before any failure: it ran and could not measure.
    expect(describeChecks(tiers).map((t) => t.notRun)).toEqual([
      false,
      false,
      false,
    ]);
    expect(
      describeChecks([
        { name: "held-back", status: "failed" },
        ...tiers.slice(1),
      ]).map((t) => [t.label, t.notRun, t.notRunBecause]),
    ).toEqual([
      ["Held-back set", false, undefined],
      ["Cost and latency", true, "Held-back set"],
      ["Model unchanged", false, undefined],
    ]);
  });

  it("puts the check that rejected a trial first and keeps the rest in order", () => {
    const tiers = [
      { name: "static", status: "passed" },
      { name: "target", status: "passed" },
      { name: "held-back", status: "failed" },
      { name: "must-pass", status: "skipped" },
      { name: "ceilings", status: "skipped" },
    ];
    expect(rejectionChecks(tiers).map((t) => [t.name, t.notRun])).toEqual([
      ["held-back", false],
      ["static", false],
      ["target", false],
      ["must-pass", true],
      ["ceilings", true],
    ]);
    // A verdict that failed nothing keeps its order.
    expect(rejectionChecks(tiers.slice(0, 2)).map((t) => t.name)).toEqual([
      "static",
      "target",
    ]);
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

  const valueOf = (request, label) =>
    heldToRows({
      ...byId("fix-search"),
      request: { ...byId("fix-search").request, ...request },
    }).find((r) => r.label === label)?.value;

  it("shows every model the fix allows and the ceilings it was set to", () => {
    const traced = {
      allowed_models: ["vertex_ai/gemini-3.8-flash", "openai/gpt-6-luna"],
      observes_models: true,
      meters_agent_cost: true,
      reply_latency_margin_pct: 40,
      cost_margin_pct: 10,
      cost_limit_usd: 0.02,
    };
    expect(valueOf(traced, "Models")).toBe(
      "vertex_ai/gemini-3.8-flash, openai/gpt-6-luna",
    );
    expect(valueOf(traced, "Reply time")).toBe("+40%");
    expect(valueOf(traced, "Agent's cost")).toBe(
      "+10% · at most $0.02 a conversation",
    );
    expect(valueOf(traced, "Model")).toBeUndefined();
  });

  it("says the lock and the cost limit can't be measured for an untraced agent", () => {
    const untraced = {
      allowed_models: [],
      observes_models: false,
      meters_agent_cost: false,
      reply_latency_margin_pct: 25,
      cost_margin_pct: 25,
      cost_limit_usd: null,
    };
    expect(valueOf(untraced, "Models")).toBe(
      "can't measure: the agent isn't traced",
    );
    expect(valueOf(untraced, "Reply time")).toBe("+25%");
    expect(valueOf(untraced, "Agent's cost")).toBe(
      "+25% · can't measure: the agent isn't traced",
    );
  });

  it("still lists what a fix from before these limits is held to", () => {
    const rows = heldToRows(byId("fix-search"));
    expect(rows.find((r) => r.label === "Models")?.value).toBe(
      "can't measure: the agent isn't traced",
    );
    expect(rows.some((r) => r.label === "Model")).toBe(false);
    expect(rows.find((r) => r.label === "Must-pass")).toBeDefined();
  });
});

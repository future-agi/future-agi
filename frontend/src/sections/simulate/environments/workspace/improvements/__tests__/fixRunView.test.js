import { describe, it, expect } from "vitest";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import {
  candidateGroups,
  ceilingCards,
  checksLine,
  endingBroke,
  endingLabel,
  endingSentence,
  endingSpent,
  fixName,
  headline,
  heldToRows,
  identityLine,
  isShipped,
  checkIcon,
  checkLabel,
  describeChecks,
  flakyProtectedText,
  noSafeNumbers,
  outcomeLabel,
  rejectionChecks,
  STATE_LABEL,
  stageLabel,
  stageOf,
  stepRows,
  verdictCards,
  winnerOf,
  measuredNotice,
} from "../fixRunView";

const byId = (id) => FIX_RUN_FIXTURES.find((f) => f.id === id);

describe("labels and identity", () => {
  it("calls a passing trial a finalist while live and Passed, not picked once finished", () => {
    expect(outcomeLabel("finalist", true)).toBe("Finalist");
    expect(outcomeLabel("finalist", false)).toBe("Passed, not picked");
    expect(outcomeLabel("winner", true)).toBe("Winner");
  });

  it("names the fix and its agent × environment, dropping unknown parts", () => {
    expect(fixName(byId("fix-ready-archive"))).toBe("Epoch 10");
    expect(identityLine(byId("fix-ready-github"))).toBe(
      "agent v8 · main @ 0487e25 × environment v1 · new env",
    );
    expect(identityLine(byId("fix-ready-archive"))).toBe(
      "agent v8 × environment v1 · new env",
    );
    expect(fixName(byId("fix-unknown-identity"))).toBe("Epoch");
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
      "Held-back pass rate 17% → 83%. 1 must-pass scenario broke.",
    );
    expect(headline(byId("fix-ready-no-held"))).toBe(
      "Target pass rate 33% → 100%. No must-pass scenario broke.",
    );
  });

  it("says why every other ending ended and what to do next, never the engine's summary", () => {
    const stopped = byId("fix-stopped");
    expect(headline(stopped)).toBe(
      `You stopped this epoch after ${stopped.candidates.length} ${stopped.candidates.length === 1 ? "candidate" : "candidates"}. Nothing changed.`,
    );
    // Stopped before the first candidate: no "after 0 candidates".
    expect(headline({ ...stopped, candidates: [] })).toBe(
      "You stopped this epoch before any candidate ran. Nothing changed.",
    );
    const old = { ...byId("fix-no-improvement"), candidates: [] };
    // A no-safe fix whose trials hold no checks says so, not the engine's summary.
    expect(headline(old)).toBe(
      "No safe improvement: no candidate passed every check. Nothing changed.",
    );
    const ending = (state, failure) =>
      headline({
        ...byId("fix-failed"),
        state,
        failure,
        report: { summary: "engine text" },
      });
    expect(ending("not_reproduced")).toMatch(
      /^The chosen scenarios did not fail often enough/,
    );
    expect(ending("out_of_budget")).toMatch(
      /Run a new epoch with a larger budget\.$/,
    );
    expect(ending("failed", { code: "fix_deadline", message: "x" })).toMatch(
      /ran out of its time limit .* Run a new epoch with a longer time limit\.$/,
    );
    expect(
      ending("failed", {
        code: "fix_engine_died",
        message: "the engine exited 137",
      }),
    ).toBe(
      "This epoch stopped because of a problem on our side, not your agent. Nothing changed. Retry it.",
    );
    // Already retried once: no Retry button, so it does not say "Retry it".
    expect(
      headline({
        ...byId("fix-failed"),
        retryable: false,
        failure: { code: "fix_engine_died", message: "x" },
      }),
    ).toBe(
      "This epoch stopped because of a problem on our side, not your agent. Nothing changed. It was retried once already. Run a new epoch from the run's Debug failures.",
    );
    // The retry itself, once it started: it was the one retry.
    const retry = {
      ...byId("fix-failed"),
      retryable: false,
      retried_from_id: "fix-earlier",
      failure: { code: "fix_engine_died", message: "x" },
    };
    expect(headline(retry)).toBe(
      "This epoch stopped because of a problem on our side, not your agent. Nothing changed. This epoch was the retry. Run a new epoch from the run's Debug failures.",
    );
    // A retry that never started took no retry: another one did since.
    expect(
      headline({
        ...retry,
        started_at: null,
        failure: { code: "fix_queue_timeout", message: "x" },
      }),
    ).toBe(
      "Too many epochs were running, so this one could not start. Nothing changed. It was retried once already. Run a new epoch from the run's Debug failures.",
    );
    for (const state of [
      "not_reproduced",
      "out_of_budget",
      "stopped",
      "too_few_cases",
      "failed",
    ]) {
      expect(
        ending(state, { code: "fix_engine_failed", message: "x" }),
      ).not.toMatch(/engine text/);
    }
  });

  it("says the protected cases a fix could not be held to by name, each once", () => {
    const fix = (rows) => ({ report: { flaky_protected: rows } });
    expect(flakyProtectedText(fix([]))).toBeNull();
    // A report from before the field says nothing.
    expect(flakyProtectedText({ report: { summary: "x" } })).toBeNull();
    expect(
      flakyProtectedText(
        fix([
          {
            key: "s3",
            name: "Escalate a large order",
            sub_goal: "a",
            passed: 2,
            runs: 3,
          },
          {
            key: "s3",
            name: "Escalate a large order",
            sub_goal: "b",
            passed: 1,
            runs: 3,
          },
          { key: "s9", name: null, sub_goal: "a", passed: 1, runs: 3 },
        ]),
      ),
    ).toBe(
      "2 protected cases are flaky on the current version, so this epoch wasn't held to them: “Escalate a large order”, “s9”.",
    );
    expect(
      flakyProtectedText(
        fix([
          {
            key: "s3",
            name: "Escalate a large order",
            sub_goal: "a",
            passed: 2,
            runs: 3,
          },
        ]),
      ),
    ).toBe(
      "1 protected case is flaky on the current version, so this epoch wasn't held to it: “Escalate a large order”.",
    );
  });

  it("says in one sentence what stopped the trials of a no-safe fix", () => {
    // Both fixture trials were rejected at the target check.
    expect(headline(byId("fix-no-improvement"))).toBe(
      "No safe improvement: neither candidate improved the target scenarios.",
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
        held_back_pass_rate: [0.39, 0.5],
      },
    });
    const real = {
      ...byId("fix-no-improvement"),
      candidates: [atHeldBack(1), atHeldBack(2)],
    };
    const said = headline(real);
    expect(said).toBe(
      "No safe improvement: both candidates improved the target scenarios but not the held-back scenarios.",
    );
    // No numbers, p-values, pipes or case keys in the headline itself.
    expect(said).not.toMatch(/\d|p =|\|/);
    // Each trial's result in plain words from its own numbers: never the engine's p-values.
    expect(noSafeNumbers(real)).toEqual({
      trials: [
        {
          attempt: 1,
          label: "Held-back scenarios",
          detail:
            "39% → 50% on scenarios the epoch never saw, not a clear gain.",
        },
        {
          attempt: 2,
          label: "Held-back scenarios",
          detail:
            "39% → 50% on scenarios the epoch never saw, not a clear gain.",
        },
      ],
    });
    const mixed = {
      ...real,
      candidates: [atHeldBack(1), byId("fix-no-improvement").candidates[1]],
    };
    expect(headline(mixed)).toBe(
      "No safe improvement: no candidate passed every check (Candidate 1 stopped at “Held-back scenarios”, Candidate 2 at “Target scenarios”).",
    );
  });

  it("leads a failed fix the user can unblock with the engine's reason, not the report's summary", () => {
    const fix = {
      ...byId("fix-failed"),
      report: { ending: "failed", summary: "The fix did not finish." },
      failure: {
        code: "engine_exited",
        message: "The engine stopped: the model provider refused the key.",
      },
    };
    expect(headline(fix)).toBe(
      "This epoch stopped on something only you can change: The engine stopped: the model provider refused the key.",
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
    expect(checksLine(tiers)).toBe("Checks · 1 of 1 passed · 2 unknown");
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
      "Change is valid",
      "Target scenarios",
      "Held-back scenarios",
      "Must-pass scenarios",
      "Holds over repeats",
      "Reply time and cost",
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
      ["Held-back scenarios", false, undefined],
      ["Reply time and cost", true, "Held-back scenarios"],
      ["Model unchanged", false, undefined],
    ]);
  });

  it("shows the numbers a ceiling was decided on, within or over", () => {
    const tiers = [
      {
        name: "ceilings",
        status: "failed",
        parts: [
          { name: "reply latency", status: "passed", detail: "within" },
          { name: "agent cost", status: "failed", detail: "over" },
        ],
        latency: {
          baseline: 13.22,
          candidate: 10.87,
          allowed: 16.53,
          limit: null,
        },
        cost: {
          baseline: 0.0096,
          candidate: 0.012,
          allowed: 0.0144,
          limit: 0.005,
        },
      },
    ];

    expect(
      describeChecks(tiers, { meters_agent_cost: true })[0].parts.map(
        (p) => p.detail,
      ),
    ).toEqual([
      "Within the limit: 10.87 s against 13.22 s on the current version, allowed up to 16.53 s.",
      "Over the limit: $0.012 against $0.0096 on the current version, allowed up to $0.0144, limit $0.005.",
    ]);
  });

  it("says a ceiling it could not measure is unknown, and why for an untraced agent", () => {
    const ceilings = (cost) => [
      {
        name: "ceilings",
        status: "skipped",
        parts: [
          { name: "reply latency", status: "passed", detail: "within" },
          cost,
        ],
      },
    ];
    const unmeasured = {
      name: "agent cost",
      status: "skipped",
      detail:
        "median agent cost per conversation not verified: the agent's model calls are not traced",
    };
    const parts = (request, cost = unmeasured) =>
      describeChecks(ceilings(cost), request)[0].parts.map((p) => [
        p.label,
        p.unknown,
        p.detail,
      ]);

    // Never traced: the cost is unknown and the line says the agent isn't traced.
    expect(parts({ meters_agent_cost: false })).toEqual([
      ["Reply time", undefined, "Within the limit."],
      [
        "Agent's cost",
        "Cost unknown",
        "This agent's model calls weren't measured. Epoch measures calls made through the OpenAI or Anthropic Python SDK on its own. For other agents, add traceAI tracing.",
      ],
    ]);
    // Traced, but this cost did not come through: why, and what makes it known.
    expect(parts({ meters_agent_cost: true })[1]).toEqual([
      "Agent's cost",
      "Cost unknown",
      "Some conversations returned no model-call record. Run again to measure them.",
    ]);
    // A reply time the conversations did not carry is unknown too.
    expect(
      describeChecks(
        [
          {
            name: "ceilings",
            status: "skipped",
            parts: [{ name: "reply latency", status: "skipped", detail: "x" }],
          },
        ],
        { meters_agent_cost: true },
      )[0].parts[0].unknown,
    ).toBe("Reply time unknown");
  });

  it("calls a check after a failed must-pass unknown when it ran, not run only when the engine skipped it", () => {
    const tiers = [
      { name: "must-pass", status: "failed", detail: "1 down" },
      { name: "repeats", status: "passed", detail: "held" },
      {
        name: "ceilings",
        status: "skipped",
        detail: "median agent cost per conversation unknown: x",
      },
      {
        name: "model-lock",
        status: "skipped",
        detail: "not run: an earlier tier failed",
      },
    ];
    const [, , ceilings, lock] = describeChecks(tiers);
    expect([ceilings.notRun, ceilings.unknown]).toEqual([false, "Unknown"]);
    expect([lock.notRun, lock.notRunBecause]).toEqual([
      true,
      "Must-pass scenarios",
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
  it("marks the current stage and fills its lines", () => {
    const rows = stepRows(byId("fix-search"));
    expect(rows.map((r) => [r.id, r.label, r.state])).toEqual([
      ["reproduce", "Reproducing", "done"],
      ["diagnose", "Diagnosing", "done"],
      ["search", "Proposing", "now"],
      ["verify", "Checking", "todo"],
    ]);
    expect(rows[0].lines).toEqual([
      "Starts from Run 1 on v8",
      "The current version varied by 17 pts between repeats.",
    ]);
    // The diagnosis leads its own stage, never the search's.
    expect(rows[1].lines).toEqual([
      "Starts from its diagnosis: The instructions never ask the agent to escalate cancellations.",
    ]);
    expect(rows[2].lines).toEqual([
      "2 of up to 4 candidates · best so far 67% on 6 target scenarios",
    ]);
    expect(rows[3].lines).toEqual([
      "Checks the best candidate on 2 held-back scenarios it never saw and 9 must-pass scenarios, including the 1 you protected",
    ]);
  });

  it("counts must-pass scenarios alone when none were protected or held back", () => {
    const fix = byId("fix-search");
    const rows = stepRows({ ...fix, plan: { ...fix.plan, protected: 0 } });
    expect(rows[3].lines).toEqual([
      "Checks the best candidate on 2 held-back scenarios it never saw and 9 must-pass scenarios",
    ]);
    const none = stepRows({
      ...fix,
      plan: { ...fix.plan, protected: 0, held_back: 0 },
    });
    expect(none[3].lines).toEqual([
      "Checks the best candidate on 9 must-pass scenarios",
    ]);
  });

  it("omits lines whose data has not arrived", () => {
    const rows = stepRows(byId("fix-unknown-identity"));
    expect(rows[0].lines).toEqual([]);
    expect(rows[3].lines).toEqual([]);
  });

  it("is diagnosing until the diagnosis or a candidate arrives, then proposing", () => {
    const fix = byId("fix-unknown-identity");
    expect(fix.diagnosis).toBeNull();
    expect(stageOf(fix)).toBe("diagnose");
    expect(stageLabel(fix)).toBe("Diagnosing");
    const diagnosing = stepRows(fix);
    expect(diagnosing.map((r) => r.state)).toEqual([
      "done",
      "now",
      "todo",
      "todo",
    ]);
    expect(diagnosing[1].lines).toEqual([
      "Reads the failures to find what to change",
    ]);
    expect(diagnosing[2].lines).toEqual([
      "Tries changes one at a time; stops early once candidates stop beating the best",
    ]);

    // A diagnosis that named no cause has still run.
    const empty = { ...fix, diagnosis: { causes: [] } };
    expect(stageOf(empty)).toBe("search");
    expect(stepRows(empty)[1]).toMatchObject({
      state: "done",
      lines: [
        "Named no single cause, so the candidates start from the failures",
      ],
    });

    // The report's diagnosis is optional: a candidate means it has moved on,
    // and the finished stage says none was recorded.
    const proposing = {
      ...fix,
      candidates: byId("fix-search").candidates.slice(0, 1),
    };
    expect(stageOf(proposing)).toBe("search");
    expect(stageLabel(proposing)).toBe("Proposing");
    expect(stepRows(proposing)[1]).toMatchObject({
      state: "done",
      lines: [
        "No diagnosis was recorded, so the candidates start from the failures",
      ],
    });
    // The issue job's summary carries no diagnosis: it reads as the search.
    expect(stageOf({ ...fix, summary: true })).toBe("search");
  });

  it("lists every cause the diagnosis found", () => {
    const rows = stepRows({
      ...byId("fix-search"),
      diagnosis: {
        causes: [
          "the instructions never ask it to escalate",
          "it replies before it looks the order up",
        ],
      },
    });
    expect(rows[1].lines).toEqual([
      "Starts from its diagnosis: The instructions never ask it to escalate.",
      "It replies before it looks the order up.",
    ]);
  });

  it("reads Queued in Reproducing's place, and Stopping on the stage it interrupts", () => {
    const queued = stepRows(byId("fix-queued"));
    expect(queued[0]).toMatchObject({
      id: "reproduce",
      label: "Queued",
      state: "now",
    });
    // An epoch that writes variations first is queued on that stage.
    const variations = stepRows({
      ...byId("fix-queued"),
      step: "variations",
      request: {
        ...byId("fix-queued").request,
        variations_pending: true,
        variation_parents: {},
      },
    });
    expect(variations.slice(0, 2).map((r) => [r.id, r.label])).toEqual([
      ["variations", "Queued"],
      ["reproduce", "Reproducing"],
    ]);
    const stopping = stepRows(byId("fix-stopping"));
    expect(stopping.map((r) => [r.id, r.label])).toEqual([
      ["reproduce", "Reproducing"],
      ["diagnose", "Diagnosing"],
      ["search", "Stopping"],
      ["verify", "Checking"],
    ]);
  });

  it("names each stage for the header", () => {
    expect(stageLabel(byId("fix-reproduce"))).toBe("Reproducing");
    expect(stageLabel(byId("fix-search"))).toBe("Proposing");
    expect(stageLabel(byId("fix-verify"))).toBe("Checking");
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
    expect(rows.map((r) => [r.id, r.label, r.state])).toEqual([
      ["variations", "Writing variations", "now"],
      ["reproduce", "Reproducing", "todo"],
      ["diagnose", "Diagnosing", "todo"],
      ["search", "Proposing", "todo"],
      ["verify", "Checking", "todo"],
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
    expect(settled.map((r) => [r.id, r.state]).slice(0, 4)).toEqual([
      ["variations", "done"],
      ["reproduce", "done"],
      ["diagnose", "done"],
      ["search", "now"],
    ]);
    expect(settled[0].lines).toEqual([
      "Kept 2 variations the current version failed: 6 failing cases",
    ]);
  });

  it("names the ending of a fix with too few failing cases", () => {
    expect(STATE_LABEL.too_few_cases).toBe("Too few failing scenarios");
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
      "unknown: this agent's model calls weren't measured. Add traceAI tracing to check them",
    );
    expect(valueOf(untraced, "Reply time")).toBe("+25%");
    expect(valueOf(untraced, "Agent's cost")).toBe(
      "+25% · unknown: this agent's model calls weren't measured. Add traceAI tracing to check them",
    );
  });

  it("still lists what a fix from before these limits is held to", () => {
    const rows = heldToRows(byId("fix-search"));
    expect(rows.find((r) => r.label === "Models")?.value).toBe(
      "unknown: this agent's model calls weren't measured. Add traceAI tracing to check them",
    );
    expect(rows.some((r) => r.label === "Model")).toBe(false);
    expect(rows.find((r) => r.label === "Must-pass")).toBeDefined();
  });
});

describe("measuredNotice", () => {
  it("shows only when the platform says it measured the agent's calls", () => {
    const fix = { request: { measured_automatically: true } };
    expect(measuredNotice(fix)).toBe(
      "Measured automatically: model, tokens and timing only. Prompts and replies are never recorded.",
    );
    expect(measuredNotice({ request: {} })).toBeNull();
    expect(
      measuredNotice({ request: { measured_automatically: false } }),
    ).toBeNull();
    expect(measuredNotice({})).toBeNull();
  });
});

describe("ceilingCards", () => {
  const withCeilings = (parts, numbers = {}) => {
    const fix = structuredClone(byId("fix-ready-archive"));
    const winner = fix.candidates.find((c) => c.outcome === "winner");
    winner.verdict.tiers.push({
      name: "ceilings",
      status: "passed",
      detail: "",
      parts,
      ...numbers,
    });
    return fix;
  };

  it("has no card without a ceilings check", () => {
    expect(ceilingCards(byId("fix-ready-archive"))).toEqual([]);
  });

  it("reads cost and reply time against the current version, or why it could not", () => {
    const fix = withCeilings(
      [
        { name: "reply latency", status: "passed", detail: "" },
        { name: "agent cost", status: "skipped", detail: "" },
      ],
      {
        latency: { baseline: 1, candidate: 1.1, allowed: 1.25, limit: null },
        cost: { baseline: null, candidate: null, allowed: null, limit: null },
      },
    );
    expect(ceilingCards(fix)).toEqual([
      {
        id: "cost",
        label: "Cost / conversation",
        value: "Not measured",
        note: "This agent's model calls weren't measured. Epoch measures calls made through the OpenAI or Anthropic Python SDK on its own. For other agents, add traceAI tracing.",
      },
      {
        id: "latency",
        label: "Reply time",
        value: "+10%",
        note: "1.10 s against 1.00 s on the current version",
      },
    ]);
  });

  it("signs a drop, says within the limit without a baseline, and skips a part it lacks", () => {
    const cheaper = withCeilings(
      [{ name: "agent cost", status: "passed", detail: "" }],
      { cost: { baseline: 0.002, candidate: 0.0019 } },
    );
    expect(ceilingCards(cheaper)).toEqual([
      {
        id: "cost",
        label: "Cost / conversation",
        value: "−5%",
        note: "$0.0019 against $0.002 on the current version",
      },
    ]);
    const noBaseline = withCeilings(
      [{ name: "reply latency", status: "passed", detail: "" }],
      { latency: { baseline: null, candidate: 1.1 } },
    );
    expect(ceilingCards(noBaseline)).toEqual([
      {
        id: "latency",
        label: "Reply time",
        value: "Within the limit",
        note: "against the current version",
      },
    ]);
  });
});

describe("candidateGroups", () => {
  const cand = (attempt, outcome, verdict) => ({
    id: `c${attempt}`,
    attempt,
    outcome,
    summary: `Change ${attempt}`,
    verdict,
  });
  const rejectedAt = (name, extra = {}) => ({
    target_pass_rate: [0.33, 0.33],
    held_back_pass_rate: null,
    must_pass_regressions: [],
    tiers: [
      { name: "static", status: "passed", detail: "" },
      { name, status: "failed", detail: "" },
    ],
    ...extra,
  });

  it("puts the winner first, then the ones not picked, then each check that rejected one", () => {
    const groups = candidateGroups(byId("fix-ready-archive"));
    expect(groups.map((g) => [g.id, g.label, g.open])).toEqual([
      ["winner", "Winner", true],
      ["finalist", "Passed, not picked", true],
      ["target", "Rejected at target scenarios", false],
    ]);
    expect(groups[0].rows).toEqual([
      expect.objectContaining({
        attempt: 3,
        target: { score: 100, delta: 67 },
        held: "17% → 83%",
        result: "Winner",
      }),
    ]);
    expect(groups[2].rows[0]).toMatchObject({
      attempt: 1,
      target: { score: 0, delta: 0 },
      held: null,
      result: "0% → 0%, not a clear gain.",
      broke: false,
    });
  });

  it("orders the furthest check first, opens held back with no winner, and keeps every candidate", () => {
    const fix = {
      ...structuredClone(byId("fix-no-improvement")),
      candidates: [
        cand(
          1,
          "rejected",
          rejectedAt("held-back", {
            target_pass_rate: [0.33, 0.67],
            held_back_pass_rate: [0.17, 0.33],
          }),
        ),
        cand(
          2,
          "rejected",
          rejectedAt("must-pass", { must_pass_regressions: ["a", "b"] }),
        ),
        cand(3, "rejected", rejectedAt("sandbox-fit")),
        cand(4, "rejected", null),
        cand(5, "pending", null),
        cand(
          6,
          "rejected",
          rejectedAt("static", {
            tiers: [{ name: "static", status: "failed", detail: "" }],
          }),
        ),
        cand(7, "rejected", rejectedAt("target")),
        cand(
          8,
          "rejected",
          rejectedAt("target", { target_pass_rate: [0.33, 0.67] }),
        ),
      ],
    };
    const groups = candidateGroups(fix);
    expect(groups.map((g) => [g.id, g.label, g.open])).toEqual([
      ["must-pass", "Rejected at must-pass scenarios", true],
      ["held-back", "Rejected at held-back scenarios", true],
      ["target", "Rejected at target scenarios", false],
      ["sandbox-fit", "Rejected at sandbox fit", false],
      ["static", "Rejected at change is valid", false],
      ["rejected", "Rejected", false],
      ["pending", "Did not run", false],
    ]);
    const byGroup = Object.fromEntries(groups.map((g) => [g.id, g.rows]));
    expect(byGroup["must-pass"][0]).toMatchObject({
      result: "Broke 2 must-pass scenarios",
      broke: true,
    });
    expect(byGroup["held-back"][0].result).toBe(
      "17% → 33% on scenarios the epoch never saw, not a clear gain.",
    );
    // High to low on target inside a group.
    expect(byGroup.target.map((r) => r.attempt)).toEqual([8, 7]);
    expect(byGroup.rejected[0]).toMatchObject({
      result: "Rejected.",
      target: null,
    });
    expect(groups.flatMap((g) => g.rows)).toHaveLength(8);
  });
});

describe("ending", () => {
  it("badges the ending with the epoch and its status", () => {
    expect(endingLabel(byId("fix-stopped"))).toBe("Epoch 3 · Stopped");
    expect(endingLabel(byId("fix-no-improvement"))).toBe(
      "Epoch 6 · No safe improvement",
    );
  });

  it("says what it spent, and on what", () => {
    expect(endingSpent(byId("fix-stopped"))).toBe(
      "Spent 412 credits before it stopped.",
    );
    expect(endingSpent(byId("fix-out-of-budget"))).toBe(
      "Spent 1,500 credits before it stopped.",
    );
    expect(endingSpent(byId("fix-not-reproduced"))).toBe(
      "Spent 412 credits on this check.",
    );
    expect(endingSpent(byId("fix-no-improvement"))).toBe("Spent 412 credits.");
    expect(
      endingSpent({ ...byId("fix-stopped"), state: "too_few_cases" }),
    ).toBe("Spent 412 credits reproducing the failing scenarios.");
  });

  it("says nothing about spend for an epoch that never started or spent nothing", () => {
    expect(endingSpent({ ...byId("fix-failed"), started_at: null })).toBeNull();
    expect(
      endingSpent({ ...byId("fix-stopped"), spent_credits: 0 }),
    ).toBeNull();
  });

  it("drops the badge's words from a no-safe sentence, and keeps every other one", () => {
    expect(endingSentence(byId("fix-no-improvement"))).toBe(
      "Neither candidate improved the target scenarios.",
    );
    expect(endingSentence(byId("fix-stopped"))).toBe(
      headline(byId("fix-stopped")),
    );
  });

  it("is red only when a candidate broke a must-pass scenario", () => {
    const fix = structuredClone(byId("fix-no-improvement"));
    expect(endingBroke(fix)).toBe(false);
    fix.candidates[0].verdict.tiers = [
      { name: "static", status: "passed", detail: "" },
      { name: "must-pass", status: "failed", detail: "" },
    ];
    expect(endingBroke(fix)).toBe(true);
    expect(endingBroke(byId("fix-failed"))).toBe(false);
  });
});

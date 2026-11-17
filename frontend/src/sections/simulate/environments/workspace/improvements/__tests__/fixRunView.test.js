import { describe, it, expect } from "vitest";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import {
  checksLine,
  fixEnding,
  fixName,
  headline,
  heldToRows,
  identityLine,
  isShipped,
  outcomeLabel,
  reviewTrialOf,
  shipAnywayLine,
  shipAnywayTrials,
  shipErrorMessage,
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

  // W10-AGENT-FIX-U05
  it("leads a fix the engine stopped with what to do next, never the engine's text", () => {
    const fix = {
      ...byId("fix-failed"),
      failure: {
        code: "fix_engine_died",
        message:
          "the improvement engine stopped 3 times before it finished; last error: KeyError: 'choices'",
      },
    };
    expect(headline(fix)).toBe(
      "The improvement stopped unexpectedly. Retry it, or contact support with improvement ID fix-failed.",
    );
    expect(headline({ ...fix, retryable: false })).toBe(
      "The improvement stopped unexpectedly. Contact support with improvement ID fix-failed.",
    );
    expect(fixEnding(fix).details).toBe(fix.failure.message);
  });

  it("gives the platform's unrecoverable-error ending the same voice, with nothing extra under Details", () => {
    const fix = {
      ...byId("fix-failed"),
      failure: {
        code: "fix_engine_failed",
        message:
          "the improvement engine hit an error it could not recover from",
      },
    };
    expect(fixEnding(fix)).toEqual({
      text: "The improvement stopped unexpectedly. Retry it, or contact support with improvement ID fix-failed.",
      details: null,
    });
  });
});

// W10-AGENT-FIX-U05, G3-UX-1
describe("shipErrorMessage", () => {
  it.each([
    [
      "fix_github_patch_failed",
      "error: patch failed: instructions.md:3",
      "This change could not be applied to the repository. Download the patch to apply it by hand.",
    ],
    [
      "fix_github_branch_changed",
      "the branch futureagi/fix-1a2b3c4d-3 changed on GitHub since the pull request was made",
      "The branch for this pull request changed on GitHub. Download the patch to apply it by hand.",
    ],
    [
      "fix_github_pull_request_failed",
      'GitHub refused the pull request: {"message":"Validation Failed"}',
      "GitHub could not open the pull request. Download the patch to apply it by hand.",
    ],
    [
      "fix_candidate_not_activated",
      "the version could not become the default: environment_busy",
      "This version could not become the default. If the environment is updating, ship again once it finishes.",
    ],
  ])("says what to do instead of the raw text on %s", (code, message, copy) => {
    expect(shipErrorMessage({ code, message, retryable: false })).toBe(copy);
  });

  it.each([
    [
      "fix_github_push_failed",
      "fatal: unable to access 'https://github.com/acme/dispatch-agent.git/': The requested URL returned error: 500",
      "This change could not be pushed to GitHub. Try again, or download the patch to apply it by hand.",
    ],
    [
      "fix_github_pull_request_failed",
      'GitHub refused the pull request: {"message":"Server Error"}',
      "GitHub could not open the pull request. Try again, or download the patch to apply it by hand.",
    ],
  ])(
    "says to try again first when the server marks %s retryable",
    (code, message, copy) => {
      expect(shipErrorMessage({ code, message, retryable: true })).toBe(copy);
    },
  );

  it("keeps the server's own sentence for other codes", () => {
    expect(
      shipErrorMessage({
        code: "fix_github_unreachable",
        message: "GitHub could not be reached",
        retryable: true,
      }),
    ).toBe("GitHub could not be reached");
  });
});

// W10-AGENT-FIX-U01
describe("a fix without a winner", () => {
  const override = (allowed, regressions = []) => ({
    allowed,
    regressions,
    reason: "",
    by: null,
    at: null,
  });
  const noWinner = () => {
    const fix = structuredClone(byId("fix-no-improvement"));
    fix.candidates[1].verdict.target_pass_rate = [0.33, 1.0];
    fix.candidates[0].override = override(false);
    fix.candidates[1].override = override(true, [
      "refund-window: policy_cited",
    ]);
    return fix;
  };

  it("reviews the winner, else the trial that did best on its targets, else the first", () => {
    const ready = byId("fix-ready-archive");
    expect(reviewTrialOf(ready).id).toBe(winnerOf(ready).id);
    expect(reviewTrialOf(noWinner()).attempt).toBe(2);
    const unrated = structuredClone(byId("fix-no-improvement"));
    unrated.candidates.forEach((c) => {
      c.verdict.target_pass_rate = null;
    });
    expect(reviewTrialOf(unrated).attempt).toBe(1);
    expect(reviewTrialOf(byId("fix-not-reproduced"))).toBeUndefined();
  });

  it("offers Ship anyway only on a finished fix with no winner, for trials the server allows", () => {
    const fix = noWinner();
    expect(shipAnywayTrials(fix).map((c) => c.attempt)).toEqual([2]);
    expect(shipAnywayTrials({ ...fix, state: "running" })).toEqual([]);
    const won = structuredClone(byId("fix-ready-archive"));
    won.candidates[0].override = override(true, ["late-cancel: escalated"]);
    expect(shipAnywayTrials(won)).toEqual([]);
  });

  it("names the broken must-pass checks without claiming every target case passed", () => {
    const fix = noWinner();
    const trial = fix.candidates[1];
    expect(shipAnywayLine(fix, trial)).toBe(
      "Trial 2 passed every check except must-pass: it broke 1 must-pass check (refund-window: policy_cited). You can still ship it with a reason.",
    );
    const shipped = {
      ...fix,
      environment: {
        ...fix.environment,
        default_agent_version_id: trial.agent_version_id,
      },
    };
    expect(shipAnywayLine(shipped, trial)).toBe(
      "Trial 2 passed every check except must-pass: it broke 1 must-pass check (refund-window: policy_cited).",
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

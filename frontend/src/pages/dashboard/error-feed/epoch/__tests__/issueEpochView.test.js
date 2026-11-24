import { describe, expect, it } from "vitest";

import {
  epochIcon,
  epochKey,
  epochLabel,
  epochLabelLong,
  epochSentence,
  failedReason,
  isEpochLive,
  primaryAction,
  secondaryActions,
} from "../issueEpochView";

const epoch = (over = {}) => ({
  id: "fix-1",
  number: 3,
  state: "running",
  step: "search",
  environment_id: "env-1",
  agent: { kind: "github" },
  environment: { default_agent_version_id: "agv-1" },
  candidates: [],
  retryable: false,
  limited_validation: false,
  ...over,
});
const launched = (over) => ({ phase: "launched", epoch: epoch(over) });
const winner = (over = {}) => ({
  id: "c-1",
  attempt: 1,
  outcome: "winner",
  agent_version_id: "agv-2",
  pull_request_url: null,
  verdict: {
    target_pass_rate: [0.2, 0.9],
    held_back_pass_rate: [0.1, 0.8],
    must_pass_regressions: [],
  },
  ...over,
});

describe("before the epoch starts", () => {
  it.each([
    [
      "drafting",
      "Drafting scenarios",
      "View drafts",
      /one builder turn, billed as authoring credits/,
    ],
    [
      "review",
      "Drafts ready",
      "Review drafts",
      /Review them, then run the epoch/,
    ],
    ["measuring", "Measuring", "View progress", /on the current version/],
  ])("%s reads as %s and opens the drawer", (phase, label, action, text) => {
    const status = { phase };
    expect(epochLabel(status)).toBe(label);
    expect(epochSentence(status)).toMatch(text);
    expect(primaryAction(status)).toEqual({ kind: "drawer", label: action });
    expect(isEpochLive(status)).toBe(true);
  });

  it("shows a blocked draft as nothing to run, not as running", () => {
    const status = { phase: "blocked" };
    expect(epochLabel(status)).toBe("Nothing to run");
    expect(epochSentence(status)).toMatch(/each one says why/);
    expect(primaryAction(status)).toEqual({
      kind: "drawer",
      label: "View drafts",
    });
    expect(isEpochLive(status)).toBe(false);
    expect(epochIcon(status).role).toBe("warning");
  });

  it("offers Stop only while the current version is measured", () => {
    expect(secondaryActions({ phase: "measuring" })).toEqual([
      { kind: "stop", label: "Stop" },
    ]);
    expect(secondaryActions({ phase: "review" })).toEqual([]);
  });

  it("offers Run Epoch on an issue with no epoch", () => {
    expect(epochKey(null)).toBeNull();
    expect(primaryAction(null)).toEqual({ kind: "run", label: "Run Epoch" });
    expect(epochIcon(null)).toBeNull();
  });
});

describe("a job that ended before its epoch", () => {
  it.each([
    ["fix_targets_passed", "Could not reproduce"],
    ["fix_targets_too_few", "Too few failing scenarios"],
    ["stopped", "Stopped"],
    ["evidence_draft_stale", "Failed"],
    ["fix_variations_source_moved", "Failed"],
    ["scenario_unavailable", "Failed"],
    ["issue_epoch_baseline_failed", "Failed"],
    ["issue_epoch_timed_out", "Failed"],
    ["issue_epoch_measuring", "Failed"],
    ["fix_run_active", "Failed"],
  ])("%s reads as %s, with Run Epoch again", (code, label) => {
    const status = { phase: "ended", ending: { code, message: "raw" } };
    expect(epochLabel(status)).toBe(label);
    expect(epochSentence(status)).not.toMatch(/raw/);
    expect(primaryAction(status)).toEqual({
      kind: "run",
      label: "Run Epoch again",
    });
    expect(isEpochLive(status)).toBe(false);
  });

  it("names why a stale draft failed, and that nothing changed", () => {
    const status = { ending: { code: "evidence_draft_stale" } };
    expect(epochLabelLong(status)).toBe("Failed: couldn't start");
    expect(epochSentence(status)).toMatch(
      /changed after the drafts were reviewed.*Nothing changed/,
    );
  });

  it("says a job past its 12-hour clock couldn't start, and that nothing changed", () => {
    const status = {
      ending: { code: "issue_epoch_timed_out", message: "raw" },
    };
    expect(epochLabelLong(status)).toBe("Failed: couldn't start");
    expect(epochSentence(status)).toMatch(
      /did not start within 12 hours.*Nothing changed/,
    );
  });

  it("calls a failed dispatch a problem on our side", () => {
    const status = { ending: { code: "issue_epoch_dispatch_failed" } };
    expect(epochLabelLong(status)).toBe("Failed: a problem on our side");
    expect(epochSentence(status)).toMatch(
      /problem on our side, not your agent/,
    );
  });

  it("says the platform's message for a refusal it does not know", () => {
    const status = {
      ending: {
        code: "usage_limit",
        message: "your workspace is out of credits",
      },
    };
    expect(epochSentence(status)).toBe("Your workspace is out of credits.");
    expect(epochLabelLong(status)).toBe(
      "Failed: something only you can change",
    );
  });
});

describe("a launched epoch", () => {
  it("says the stage while it runs, in the epoch page's words, and opens the epoch", () => {
    // A search with no diagnosis yet is still diagnosing.
    const status = launched();
    expect(epochLabel(status)).toBe("Running");
    expect(epochSentence(status)).toBe("Epoch 3 is diagnosing the failures.");
    expect(primaryAction(status)).toEqual({
      kind: "view",
      label: "View Epoch 3",
    });
    expect(epochIcon(status).tip).toBe("Epoch 3 · Running · Diagnosing");
    expect(epochIcon(status).role).toBe("info");

    const proposing = launched({ diagnosis: { causes: ["a cause"] } });
    expect(epochSentence(proposing)).toBe("Epoch 3 is proposing changes.");
    expect(epochIcon(proposing).tip).toBe("Epoch 3 · Running · Proposing");
    // The job's summary carries no diagnosis: it reads as the search.
    const summary = launched({ summary: true });
    expect(epochIcon(summary).tip).toBe("Epoch 3 · Running · Proposing");
    expect(epochIcon(launched({ step: "verify" })).tip).toBe(
      "Epoch 3 · Running · Checking",
    );
  });

  it("makes Open pull request the main action on a ready GitHub epoch", () => {
    const status = launched({ state: "ready", candidates: [winner()] });
    expect(epochLabel(status)).toBe("Ready for review");
    expect(primaryAction(status)).toEqual({
      kind: "ship",
      label: "Open pull request",
    });
    expect(secondaryActions(status)).toEqual([
      { kind: "view", label: "View Epoch 3" },
    ]);
    expect(epochSentence(status)).toBe(
      "Epoch 3 found a change that passed every check.",
    );
  });

  it("makes Make default the main action on a ready uploaded agent", () => {
    const fix = epoch({
      state: "ready",
      agent: { kind: "archive" },
      candidates: [winner()],
    });
    expect(primaryAction({ epoch: fix })).toEqual({
      kind: "ship",
      label: "Make default",
    });
  });

  it("opens the epoch once an uploaded winner is the default", () => {
    const status = launched({
      state: "ready",
      agent: { kind: "archive" },
      candidates: [winner({ agent_version_id: "agv-1" })],
    });
    expect(primaryAction(status)).toEqual({
      kind: "view",
      label: "View Epoch 3",
    });
    expect(epochSentence(status)).toMatch(/environment's default now/);
  });

  it("links the pull request once it is open", () => {
    const status = launched({
      state: "ready",
      candidates: [
        winner({ pull_request_url: "https://github.com/o/r/pull/9" }),
      ],
    });
    expect(primaryAction(status)).toEqual({
      kind: "ship",
      label: "View pull request",
    });
    expect(epochSentence(status)).toMatch(/Its pull request is open/);
  });

  it("says Limited validation, and a draft pull request on GitHub", () => {
    const status = launched({
      state: "ready",
      limited_validation: true,
      candidates: [winner()],
    });
    expect(epochSentence(status)).toMatch(
      /Limited validation: .* opens as a draft/,
    );
  });

  it("says Proven on the targets only when nothing was held back", () => {
    const status = launched({
      state: "ready",
      candidates: [
        winner({
          verdict: {
            target_pass_rate: [0.2, 0.9],
            held_back_pass_rate: null,
            must_pass_regressions: [],
          },
        }),
      ],
    });
    expect(epochSentence(status)).toMatch(/Proven on the targets only/);
  });

  it.each([
    ["no_improvement", "No safe improvement", /Nothing changed/],
    ["not_reproduced", "Could not reproduce", /did not fail often enough/],
    ["out_of_budget", "Stopped at budget", /larger budget/],
    ["stopped", "Stopped", /was stopped before any candidate ran/],
    ["too_few_cases", "Too few failing scenarios", /pick other traces/],
  ])("%s reads as %s and offers Run Epoch again", (state, label, text) => {
    const status = launched({ state });
    expect(epochLabel(status)).toBe(label);
    expect(epochSentence(status)).toMatch(text);
    expect(primaryAction(status)).toEqual({
      kind: "run",
      label: "Run Epoch again",
    });
    expect(secondaryActions(status)).toEqual([
      { kind: "view", label: "View Epoch 3" },
    ]);
  });

  it("splits Failed four ways (#77)", () => {
    expect(failedReason("fix_deadline")).toBe("ran out of time");
    expect(failedReason("fix_queue_timeout")).toBe("couldn't start");
    expect(failedReason("fix_engine_died")).toBe("a problem on our side");
    expect(failedReason(undefined)).toBe("a problem on our side");
    expect(failedReason("fix_agent_crashed")).toBe(
      "something only you can change",
    );
  });

  it("retries a failed epoch once, then offers a new one", () => {
    const failure = { code: "fix_engine_died", message: "x" };
    const first = launched({ state: "failed", failure, retryable: true });
    expect(primaryAction(first)).toEqual({ kind: "retry", label: "Retry" });
    expect(epochSentence(first)).toBe(
      "Epoch 3 stopped because of a problem on our side, not your agent. Nothing changed. Retry it.",
    );
    expect(secondaryActions(first)).toEqual([
      { kind: "view", label: "View Epoch 3" },
      { kind: "run", label: "Run Epoch again" },
    ]);
    const retried = launched({ state: "failed", failure, retryable: false });
    expect(primaryAction(retried)).toEqual({
      kind: "run",
      label: "Run Epoch again",
    });
  });

  it("quotes what only the user can change, as a sentence", () => {
    const status = launched({
      state: "failed",
      failure: {
        code: "fix_agent_crashed",
        message: "the agent exited on start",
      },
    });
    expect(epochSentence(status)).toBe(
      "Epoch 3 stopped on something only you can change: The agent exited on start.",
    );
    expect(epochIcon(status)).toEqual({
      icon: "solar:danger-triangle-linear",
      role: "danger",
      tip: "Epoch 3 · Failed: something only you can change",
    });
  });

  it("gives each ending its own icon", () => {
    const icons = [
      "ready",
      "no_improvement",
      "not_reproduced",
      "too_few_cases",
      "out_of_budget",
      "stopped",
      "failed",
    ].map((state) => epochIcon(launched({ state })).icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it("never says the words #77 rules out", () => {
    const states = [
      "queued",
      "running",
      "stopping",
      "ready",
      "no_improvement",
      "not_reproduced",
      "out_of_budget",
      "stopped",
      "too_few_cases",
      "failed",
    ];
    const words = states.flatMap((state) => {
      const status = launched({ state, candidates: [winner()] });
      return [
        epochLabelLong(status),
        epochSentence(status),
        primaryAction(status).label,
        epochIcon(status).tip,
      ];
    });
    for (const text of words) {
      expect(text).not.toMatch(
        /Verified|Inconclusive|Suggestion only|Improvement|\bFix\b|RSI/,
      );
    }
  });
});

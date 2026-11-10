const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export const READY_TO_RUN_COPY = {
  title: "Ready to run",
  summary: ({ eligible, requested, blocked }, blockers) =>
    [
      `${eligible} of ${requested} eligible`,
      `${blocked} blocked`,
      blockers
        ? `${plural(blockers, "blocker", "blockers")} to fix`
        : "no blocker",
    ].join(" · "),
  recheck: "Re-check",
  checking: "Checking…",
  areas: {
    start_and_message: "Starts and replies",
    tool_binding: "Tools wired to the test world",
    reset: "World resets",
    cancellation: "Run can be stopped",
    evidence: "Evidence the checks need",
    grading: "Grading coverage",
  },
  states: {
    unchecked: "Not checked yet for these versions. Re-check to check them.",
    checking:
      "Checking these versions together. Runs wait until it finishes, at most 45 minutes.",
    failed: (reason) =>
      `The last check failed${reason ? `: ${reason}` : ""}. Runs of these versions are refused until a check passes.`,
    ours: (reason) =>
      `Check unavailable${reason ? `: ${reason}` : " on our side"}. Runs aren't held.`,
    retry: "The last check didn't finish. Re-check to try again.",
  },
  affected: (n) => `${n} affected`,
  fix: "Fix",
  hideFix: "Hide",
  after: (would) => `After the fix: ${would}`,
  recheckPair: "Re-check this pair",
  retryAgent: "Retry the agent's start",
  moreReady: (labels, all) =>
    `${all ? "All" : labels.length} ${all ? "ready" : "more ready"}: ${labels
      .map((label) => label.toLowerCase())
      .join(", ")}`,
  notGradable: (n) =>
    `${plural(n, "scenario has", "scenarios have")} no check that can grade it`,
  loadError: "Couldn't check whether these versions are ready to run.",
};

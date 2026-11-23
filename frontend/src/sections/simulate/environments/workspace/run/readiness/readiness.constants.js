const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export const READY_TO_RUN_COPY = {
  title: "Ready to run",
  blockers: (n) =>
    n ? `${plural(n, "blocker", "blockers")} to fix` : "No blocker",
  namesMore: (n) => `and ${n} more`,
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
    unchecked: "Not checked yet for these versions · re-check",
    checking: "Checking these versions · runs wait until it finishes",
    failed: (reason) =>
      `Last check failed${reason ? `: ${reason}` : ""} · runs wait until a check passes`,
    ours: (reason) =>
      `Check unavailable${reason ? `: ${reason}` : " on our side"} · runs aren't held`,
    retry: "The last check didn't finish · re-check",
    unavailable: "These versions can't be checked right now",
    refused: (reason) =>
      `The check couldn't start${reason ? `: ${reason}` : ""}`,
  },
  affected: (n) => `${n} affected`,
  blockedCount: (n) => `${n} blocked`,
  details: (label) => `Details for ${label}`,
  // The labels of an opened line's rows.
  grid: {
    saw: "What we saw",
    fix: "Fix",
    would: "Once fixed",
    waits: "Waits on it",
    checks: "What it checks",
    lastCheck: "Last check",
    why: "Why again",
  },
  // What each area's check looks at, for an opened re-check line.
  checks: {
    start_and_message:
      "The agent starts in the test world and answers a first message.",
    tool_binding: "Each tool call lands in the test world, not production.",
    reset: "The world goes back to its seeded state between scenarios.",
    cancellation: "A run can be stopped part way through.",
    evidence: "The run records what the scenario checks need to read.",
    grading: "Each scenario has a check that can grade it.",
  },
  recheckPair: "Re-check this pair",
  retryAgent: "Retry the agent's start",
  openAgentVersions: "Open agent versions",
  moreReady: (labels, all) =>
    `${all ? "All ready" : "Also ready"}: ${labels.join(", ")}`,
  notGradable: (n) =>
    `${plural(n, "scenario has", "scenarios have")} no check that can grade it`,
  loadError: "Couldn't check whether these versions are ready to run.",
  noVersions: "Ready to run starts once this environment has versions.",
};

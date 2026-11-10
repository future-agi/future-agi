const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export const READY_TO_RUN_COPY = {
  title: "Ready to run",
  summary: ({ eligible, requested, blocked, stale }, blockers) =>
    [
      `${eligible} of ${requested} eligible`,
      `${blocked} blocked`,
      stale ? `${stale} not proved here` : null,
      blockers
        ? `${plural(blockers, "blocker", "blockers")} to fix`
        : "no blocker",
    ]
      .filter(Boolean)
      .join(" · "),
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
    `${all ? "All ready" : "Also ready"}: ${labels
      .map((label) => label.toLowerCase())
      .join(", ")}`,
  notGradable: (n) =>
    `${plural(n, "scenario has", "scenarios have")} no check that can grade it`,
  loadError: "Couldn't check whether these versions are ready to run.",
};

export const RUN_PLAN_COPY = {
  title: "Run plan",
  subtitle: (envName, envLabel) =>
    [envName, envLabel && `environment ${envLabel}`]
      .filter(Boolean)
      .join(" · "),
  heldBack: ({ requested, blocked }, label) =>
    `${requested} selected · ${requested - blocked} will run · ${blocked} held back${
      label ? ` (${label.toLowerCase()})` : ""
    }, recorded as not run`,
  allHeld: (label) =>
    `No run: every selected scenario is held back${label ? ` (${label})` : ""}. Fix it, or pick scenarios it doesn't hold back.`,
  runOf: (will, requested) => `Run ${will} of ${requested}`,
};

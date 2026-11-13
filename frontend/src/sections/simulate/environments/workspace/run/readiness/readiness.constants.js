const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export const READY_TO_RUN_COPY = {
  title: "Ready to run",
  blockers: (n) =>
    n ? `${plural(n, "blocker", "blockers")} to fix` : "No blocker",
  // The plan's count line; a part with a bucket names its scenarios on hover.
  counts: ({ eligible, requested, blocked, stale }) =>
    [
      {
        bucket: "eligible",
        count: eligible,
        text: `${eligible} of ${requested} eligible`,
      },
      { bucket: "blocked", count: blocked, text: `${blocked} blocked` },
      stale
        ? { bucket: "stale", count: stale, text: `${stale} not proved here` }
        : null,
    ].filter(Boolean),
  namesLoading: "Loading the scenarios…",
  namesMore: (n) => `and ${n} more`,
  namesError: "Couldn't load these scenarios.",
  namesTooMany: "Names show when 200 or fewer scenarios are selected.",
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
  saveAndRecheck: "Save and re-check",
  credentialsNote:
    "Only these values change; everything else on the agent keeps its value. Saving checks the pair again.",
  recheckPair: "Re-check this pair",
  retryAgent: "Retry the agent's start",
  moreReady: (labels, all) =>
    `${all ? "All ready" : "Also ready"}: ${labels.join(", ")}`,
  notGradable: (n) =>
    `${plural(n, "scenario has", "scenarios have")} no check that can grade it`,
  loadError: "Couldn't check whether these versions are ready to run.",
  groups: {
    needs_you: "Needs you",
    builder: "Builder chat can fix",
    recheck: "Re-check to confirm",
  },
  groupHints: {
    needs_you: "Only you can change these",
    builder: "The builder chat can make these changes",
    recheck: "Checking again settles these",
  },
  selectAll: "Select every line the builder chat can fix",
  selectLine: (label) => `Fix ${label.toLowerCase()} with the builder chat`,
  sendSelected: (count) =>
    count ? `Fix ${count} with builder chat` : "Tick lines to send",
  // With nothing new ticked, the button says the fix is under way.
  addSelected: (count) => (count ? `Add ${count} to this fix` : "Fixing…"),
  fixOne: "Fix just this one",
  addOne: "Add just this one to the fix",
  fixesUnsaved:
    "Save or discard the unsaved changes first, then send fixes to the chat.",
  fixRefused: {
    unsaved_changes: "Save or discard the unsaved changes first, then Fix all.",
    fix_running: "A fix is already running for this environment.",
    chat_unavailable: "The builder chat isn't available for this environment.",
    nothing_to_fix:
      "Nothing left for the chat to fix here. Re-check to see the latest.",
    default: "Couldn't start the fix. Try again in a moment.",
  },
  sendsToChat: "Sends this to the builder chat",
  chatFixing: (labels) =>
    labels.length ? `Fixing: ${labels.join(", ")}` : "Fixing",
  chatFollow: "follow it in the builder chat",
  goToChat: "Go to chat",
  progress: {
    queued: "Queued",
    fixing: "Fixing…",
    waiting_on_you: "Waiting on you",
    waiting_on_other: (label) =>
      `Waiting on ${(label || "another line").toLowerCase()}`,
    fixed: "Fixed",
    could_not_fix: "Couldn't fix",
  },
};

export const RUN_PLAN_COPY = {
  title: "Run plan",
  collapse: "Collapse run plan",
  expand: "Expand run plan",
  close: "Close",
  subtitle: (envName, envLabel) =>
    [envName, envLabel && `environment ${envLabel}`]
      .filter(Boolean)
      .join(" · "),
  // The version pickers' labels.
  pickers: { agent: "Agent", environment: "Environment", suite: "Scenarios" },
  // Beside the scenario picker; null is "every current scenario".
  scenarioCount: (n, all) => {
    if (!all) return plural(n, "selected scenario", "selected scenarios");
    if (n === null) return "All scenarios";
    return `All ${plural(n, "scenario", "scenarios")}`;
  },
  heldBack: ({ requested, blocked }, label) =>
    `${requested} selected · ${requested - blocked} will run · ${blocked} held back${
      label ? ` (${label.toLowerCase()})` : ""
    }, recorded as not run`,
  allHeld: (label) =>
    `Every selected scenario waits on a fix${label ? `: ${label.toLowerCase()}` : ""}`,
  runOf: (will, requested) => `Run ${will} of ${requested}`,
  // The builder chat's card when a fix ends.
  fixEnd: {
    open: (labels) =>
      `Fixed. ${labels.length} still to fix in the run plan: ${labels.join(", ")}.`,
    clear: "The pair passed its check. Nothing left to fix before the run.",
    openPlan: "Open run plan",
    review: "Review and start run",
  },
};

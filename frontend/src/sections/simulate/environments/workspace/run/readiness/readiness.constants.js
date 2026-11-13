const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export const READY_TO_RUN_COPY = {
  title: "Ready to run",
  // The header's parts; a part with a bucket names its scenarios on hover.
  summary: ({ eligible, requested, blocked, stale }, blockers) =>
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
      {
        text: blockers
          ? `${plural(blockers, "blocker", "blockers")} to fix`
          : "no blocker",
      },
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
    unchecked: "Not checked yet for these versions. Re-check to check them.",
    checking: "Checking these versions together. Runs wait until it finishes.",
    failed: (reason) =>
      `The last check failed${reason ? `: ${reason}` : ""}. Runs of these versions are refused until a check passes.`,
    ours: (reason) =>
      `Check unavailable${reason ? `: ${reason}` : " on our side"}. Runs aren't held.`,
    retry: "The last check didn't finish. Re-check to try again.",
    unavailable: "These versions can't be checked right now.",
    refused: (reason) =>
      `The check couldn't start${reason ? `: ${reason}` : ""}.`,
  },
  affected: (n) => `${n} affected`,
  details: (label) => `Details for ${label}`,
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
  addSelected: (count) =>
    count ? `Add ${count} to this fix` : "Tick lines to add",
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
  sendsToChat: "Sends this to the builder chat:",
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
  heldBack: ({ requested, blocked }, label) =>
    `${requested} selected · ${requested - blocked} will run · ${blocked} held back${
      label ? ` (${label.toLowerCase()})` : ""
    }, recorded as not run`,
  allHeld: (label) =>
    `No run: every selected scenario is held back${label ? ` (${label})` : ""}. Fix it, or pick scenarios it doesn't hold back.`,
  runOf: (will, requested) => `Run ${will} of ${requested}`,
};

export const CALL_TRACES_COPY = {
  tab: "Traces",
  empty: "No trace for this call",
  error: "Couldn't load this call's traces.",
  noRoot: "Root span not stored yet",
};

// A scroll pane keeps ↑/↓ for itself while focused, so the arrows scroll it
// instead of stepping to another conversation, as the voice transcript does.
// Focusable so a click inside hands it the keyboard; default not
// prevented, so the browser still scrolls.
export const ownsArrowKeys = {
  tabIndex: 0,
  onKeyDown: (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") e.stopPropagation();
  },
};

export const CALL_WORKERS_COPY = {
  tab: "Participants",
  error: "Couldn't load this call's participants.",
  unknown: "Unknown",
  noEvidence:
    "Nothing was recorded for this call's participants: the app sent no traces and the sandbox recorded no hand-offs.",
  partialCollection:
    "Only part of this call's traces could be read, so some steps may be missing.",
  unavailableCollection:
    "This call's traces could not be read, so its steps may be missing.",
  eventsOmitted: (count) =>
    `${count} more step${count === 1 ? "" : "s"} not shown: this call's record reached its size limit`,
  sandboxOnly:
    "Built from the sandbox's record of this call; the app sent no traces, so the Traces tab is empty.",
  // What multi-agent testing covers, said on every multi-agent call, so nothing reads as more than it is.
  supportedBoundary:
    "Multi-agent testing covers a coordinator handing work to its workers one at a time, one level deep.",
  // Added where a call went past it.
  beyondBoundary:
    "This call also had nested, parallel or background hand-offs: they are shown as recorded, outside that support.",
  // The API's counts of how the call's hand-offs ended.
  handOffSummary: ({
    hand_offs: handOffs,
    workers,
    finished,
    did_not_finish: didNotFinish,
    passed,
    cut_off: cutOff,
  }) => {
    const ends = [
      finished && `${finished} finished`,
      didNotFinish && `${didNotFinish} didn't`,
      passed && `${passed} passed control`,
      cutOff && `${cutOff} cut off`,
    ].filter(Boolean);
    return `${handOffs} hand-off${handOffs === 1 ? "" : "s"} to ${workers} worker${workers === 1 ? "" : "s"}. ${ends.join(", ")}.`;
  },
  // Why a call has no record to show, by the state the API serves.
  states: {
    unreadable:
      "This call's participants couldn't be read. Its record may have hand-offs that aren't shown.",
    not_kept:
      "This attempt ran before attempts kept their participants, so its record wasn't kept.",
  },
  workersNotObserved: (names) => `Not observed: ${names}`,
  // Took part (its tool calls, or control handed to it) with no return recorded: no end is claimed for it.
  workersNoEnd: (names) => `Took part, no end recorded: ${names}`,
  // A worker's end state when the trial cut it short; one that returned or never ran has none shown.
  cutShort: {
    no_return: "no return before the call ended",
    deadline_passed: "past deadline",
    cancel_requested: "cancel requested",
    cancelled: "cancelled",
    infrastructure: "infrastructure fault",
  },
  workersCutShort: (list) => `Cut short: ${list}`,
  // A worker's own checks: beside the call's verdict, never deciding it.
  workerChecks: "Worker checks",
  workerCheckResult: (passed, result) =>
    passed === true
      ? "Passed"
      : passed === false
        ? "Failed"
        : {
            not_applicable: "Not applicable",
            inconclusive: "Unsure",
            unmeasured: "Not measured",
          }[result],
  // Each failed or unsure check on the step its evidence points to, or why it is on none.
  placedChecks: "Failed and unsure checks",
  checkStatus: { failed: "Failed", inconclusive: "Unsure" },
  // A worker's check sits on its worker's one hand-off; any other says why it is on no step.
  onHandOff: "On its hand-off",
  untied: {
    no_moment:
      "Not tied to a step: nothing recorded ties this check to a moment in the call",
    several_hand_offs:
      "Not tied to a step: this worker was handed work more than once",
    no_hand_off: "Not tied to a step: this worker was never handed work",
  },
  // Past its action deadline: the world refused its later calls. Its execution is stopped only when the app serves it
  // as its own process (cleanup says "Stopped when its deadline passed").
  workersPastDeadline: (names) => `Past their action deadline: ${names}`,
  workersBypassed: (names) =>
    `Reached the world on the shared address, not their own channel, so their limits weren't held and their own checks weren't graded: ${names}`,
  workersNotDeclared: (names) => `Not declared: ${names}`,
  // What happened to a cut-short worker when the call ended. "stopped" for one worker only when the app serves it as
  // its own process; one inside the app's process stops with the run's whole sandbox.
  cleanup: {
    stopped: (time) => `Stopped when its deadline passed, ${time}`,
    stopped_with_sandbox: (time) =>
      `Stopped when the run's sandbox was removed, ${time}`,
    still_running: "Still running when the call ended",
    unknown: "Cleanup not confirmed",
  },
  workerCleanup: (name, cleanup) => `${name}: ${cleanup}`,
  stoppedByUser:
    "Stopped by the user during this call. What its participants did up to the stop is shown.",
  // Scoped to the participants: the transcript up to the stop is kept and shown on its own tab.
  stoppedByUserNoEvidence:
    "Stopped by the user before any participant's work was recorded.",
};

export const TRIAL_PICKER_COPY = {
  label: "Trial",
  tooltip: (n, verdict) => `Trial ${n}, ${verdict}`,
};

// A run status's colour, by the role STATUS_META gives it.
export const STATUS_ROLE_COLOR = {
  success: "success.main",
  danger: "error.main",
  warning: "warning.main",
  info: "info.main",
  neutral: "text.disabled",
};

// A scenario's trials lined up by route, as the trial-routes API serves them.
export const TRIAL_ROUTES_COPY = {
  trials: (count) => `${count} trials`,
  splitAt: "Split at ",
  sameRoute: "Every trial took the same route",
  show: "Show trials",
  hide: "Hide trials",
  showTools: "Show tool calls",
  trial: "Trial",
  result: "Result",
  trialN: (n) => `Trial ${n}`,
  unknown: "Unknown",
  // What a trial did at a step: asked a worker (or called a tool), and how it went.
  cell: (state, kind, count) =>
    ({
      step: kind === "tool_call" ? "Called" : "Asked",
      failed: "Failed",
      no_answer: "No answer",
      cut_off: "Cut off",
      repeated: `×${count}`,
      absent: "Not asked",
    })[state] ?? state,
  splitGroup: (word, trials) => `${word} in ${trials.join(", ")}`,
  openStep: (n) => `Open trial ${n} at this step`,
  // A trial the server lists but doesn't compare, and why.
  notCompared: {
    partial: "Not compared: its record was read only in part",
    pending: "Not compared: its record isn't read yet",
    not_finished: "Not compared: the trial didn't finish",
    unreadable: "Not compared: its record couldn't be read",
  },
};

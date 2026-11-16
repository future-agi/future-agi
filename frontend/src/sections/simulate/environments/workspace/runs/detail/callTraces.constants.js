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
  coordinator: "Coordinator",
  unknown: "Unknown",
  turn: (n) =>
    n === null
      ? "Turn not recorded"
      : n === 0
        ? "Before the caller spoke"
        : `Turn ${n}`,
  // The record does not say which hand-off this ran in, so it sits at the top of its turn.
  parentUnknown: "parent unknown",
  noEvidence:
    "Nothing was recorded for this call's participants: the app sent no traces and the sandbox recorded no hand-offs.",
  partialCollection:
    "Only part of this call's traces could be read, so some steps may be missing.",
  unavailableCollection:
    "This call's traces could not be read, so its steps may be missing.",
  modelTokens: (model, tokens) =>
    [model, tokens ? `${tokens.toLocaleString()} tokens` : null]
      .filter(Boolean)
      .join(" · "),
  openInTraces: "Open in Traces",
  inParallel: "in parallel",
  payloadOmitted: "Not kept: this call's record reached its size limit",
  eventsOmitted: (count) =>
    `${count} more step${count === 1 ? "" : "s"} not shown: this call's record reached its size limit`,
  request: "Caller's request",
  noRequest: "No request recorded",
  response: "Coordinator's reply",
  noReply: "No reply recorded",
  transferred: (name) => `Control passed to ${name}`,
  sandboxOnly:
    "Built from the sandbox's record of this call; the app sent no traces, so the Traces tab is empty.",
  // What multi-agent testing covers, said on every multi-agent call, so nothing reads as more than it is.
  supportedBoundary:
    "Multi-agent testing covers a coordinator handing work to its workers one at a time, one level deep.",
  // Added where a call went past it.
  beyondBoundary:
    "This call also had nested, parallel or background hand-offs: they are shown as recorded, outside that support.",
  noResult: "No result recorded",
  noReturn: (name) => `No return from ${name} before the call ended`,
  returnCutOff: (name) =>
    `Return from ${name} not shown: this call's record is incomplete`,
  failed: "error",
  input: "Input",
  output: "Output",
  noPayload: "Nothing recorded",
  ok: "ok",
  showJson: "Show JSON",
  hideJson: "Hide JSON",
  caller: "Caller",
  messageStep: "Message",
  handOffStep: "Hand-off",
  controlPassStep: "Control pass",
  toolCallStep: "Tool call",
  answerStep: "Answer",
  noReturnStep: "No return",
  notShownStep: "Not shown",
  replyStep: "Reply",
  showMore: "Show more",
  showLess: "Show less",
  rawEvents: "Raw events",
  workerColumn: "Worker",
  taskColumn: "Task",
  resultColumn: "Result",
  showRow: (name) => `Show raw data for ${name}`,
  hideRow: (name) => `Hide raw data for ${name}`,
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
  callsOutsideTable: (count) =>
    `${count} tool call${count === 1 ? "" : "s"} had no hand-off, so ${count === 1 ? "it isn't" : "they aren't"} in this table. Turn on Raw events to see ${count === 1 ? "it" : "them"}.`,
  // Why a call has no record to show, by the state the API serves.
  states: {
    unreadable:
      "This call's participants couldn't be read. Its record may have hand-offs that aren't shown.",
    not_kept:
      "This attempt ran before attempts kept their participants, so its record wasn't kept.",
  },
  workersNotObserved: (names) => `Not observed: ${names}`,
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

// The Participants tab's layout: how deep a row indents, how fast a fold opens, the JSON button's size.
// Indent levels in the table's narrow Worker column; deeper rows stay at the last one.
export const MAX_ROW_INDENT = 3;

export const MOTION_MS = 180;
export const NO_MOTION_SX = {
  "@media (prefers-reduced-motion: reduce)": { transition: "none !important" },
};

export const JSON_BUTTON_SX = {
  fontSize: 11,
  lineHeight: 1,
  fontWeight: 500,
  height: 20,
  minHeight: 20,
  minWidth: 0,
  px: 0.75,
  py: 0,
  borderRadius: "5px",
  borderColor: "divider",
  color: "text.secondary",
  "& .MuiButton-startIcon": { mr: 0.5, ml: 0 },
};

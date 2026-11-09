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
  // What multi-agent testing covers, said where a call went past it, so nothing reads as more than it is.
  beyondBoundary:
    "Multi-agent testing covers a coordinator handing work to its workers one at a time, one level deep. This call also had nested or parallel hand-offs: they are shown as recorded, outside that support.",
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
  handOffSummary: ({
    handOffs,
    workers,
    finished,
    didNotFinish,
    passed,
    cutOff,
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
  workersNotObserved: (names) => `Not observed: ${names}`,
  // A worker's end state when the trial cut it short; one that returned or never ran has none shown.
  cutShort: {
    no_return: "no return before the call ended",
    timed_out: "timed out",
    cancel_requested: "cancel requested",
    cancelled: "cancelled",
    infrastructure: "infrastructure fault",
  },
  workersCutShort: (list) => `Cut short: ${list}`,
  workersNotDeclared: (names) => `Not declared: ${names}`,
};

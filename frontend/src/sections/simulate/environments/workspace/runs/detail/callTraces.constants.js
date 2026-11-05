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
  notObserved: "not observed",
  undeclared: "not declared",
  intro:
    "How the coordinator handed work to the other participants in this call: each hand-off, the tools each one used, and what it returned.",
  seenFlat: "One level, one hand-off at a time",
  seen: (depth, parallel, overlapUnknown) =>
    `${depth === 1 ? "One level" : `${depth} levels deep`} · ${
      parallel
        ? `${parallel} hand-off${parallel === 1 ? "" : "s"} ran in parallel`
        : overlapUnknown
          ? "can't tell if hand-offs overlapped, some have no start time"
          : "one hand-off at a time"
    }`,
  inParallel: "in parallel",
  // The record does not say which hand-off this ran in, so it sits under the coordinator.
  parentUnknown: "parent unknown",
  noEvidence:
    "Nothing was recorded for this call's participants: the app sent no traces and the sandbox recorded no hand-offs.",
  payloadOmitted: "Not kept: this call's record reached its size limit",
  eventsOmitted: (count) =>
    `${count} more step${count === 1 ? "" : "s"} not shown: this call's record reached its size limit`,
  workersSummary: (total, observed, missing) =>
    `${total} participants · ${observed} observed · ${missing} not observed`,
  showWorkers: "Show all participants",
  hideWorkers: "Hide participants",
  request: "Caller's request",
  noRequest: "No request recorded",
  response: "Coordinator's reply",
  noReply: "No reply recorded",
  turn: (n) => (n === 0 ? "Before the caller spoke" : `Turn ${n}`),
  delegated: (name) => `Delegated to ${name}`,
  transferred: (name) => `Control passed to ${name}`,
  sandboxOnly:
    "Built from the sandbox's record of this call; the app sent no traces, so the Traces tab is empty.",
  returned: (name) => `${name} returned`,
  noResult: "No result recorded",
  noReturn: (name) => `No return from ${name} before the call ended`,
  returnCutOff: (name) =>
    `Return from ${name} not shown: this call's record reached its size limit`,
  failed: "error",
  input: "Input",
  output: "Output",
  noPayload: "Nothing recorded",
  showMore: "Show more",
  showLess: "Show less",
};

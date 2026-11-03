export const CALL_TRACES_COPY = {
  tab: "Traces",
  empty: "No trace for this call",
  error: "Couldn't load this call's traces.",
  noRoot: "Root span not stored yet",
};

export const CALL_WORKERS_COPY = {
  tab: "Participants",
  none: "No workers recorded for this call. Hand-offs to other agents show here once they are seen, which can take a minute after the call.",
  error: "Couldn't load this call's workers.",
  coordinator: "Coordinator",
  unknown: "Unknown",
  notObserved: "not observed",
  undeclared: "not declared",
  seenFlat: "Seen: one level, one at a time",
  seen: (depth, parallel) =>
    `Seen: ${depth === 1 ? "one level" : `${depth} levels deep`}, ${
      parallel
        ? `${parallel} hand-off${parallel === 1 ? "" : "s"} in parallel`
        : "one at a time"
    }`,
  scope:
    "Checked for one coordinator handing work to its workers one at a time, one level deep; deeper or parallel hand-offs are shown as seen.",
  inParallel: "in parallel",
  payloadOmitted: "Not kept: this call's record reached its size limit",
  eventsOmitted: (count) =>
    `${count} more step${count === 1 ? "" : "s"} not shown: this call's record reached its size limit`,
  workersSummary: (total, observed, missing) =>
    `${total} workers · ${observed} observed · ${missing} not observed`,
  showWorkers: "Show all workers",
  hideWorkers: "Hide workers",
  request: "Caller's request",
  response: "Coordinator's reply",
  delegated: (name) => `Delegated to ${name}`,
  returned: (name) => `${name} returned`,
  failed: "error",
  input: "Input",
  output: "Output",
  showDetail: "Input and output",
};

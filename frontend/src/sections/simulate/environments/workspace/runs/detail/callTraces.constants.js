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
  intro:
    "How the coordinator handed work to other agents in this call: each hand-off, the tools that agent used, and what it returned.",
  noHandOffs: "This agent didn't hand work to other agents in this call.",
  seenFlat: "One level, one hand-off at a time",
  seen: (depth, parallel) =>
    `${depth === 1 ? "One level" : `${depth} levels deep`} · ${
      parallel
        ? `${parallel} hand-off${parallel === 1 ? "" : "s"} ran in parallel`
        : "one hand-off at a time"
    }`,
  scope:
    "Checked for one coordinator handing work one at a time; deeper or parallel hand-offs are shown as they happened.",
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
  transferred: (name) => `Control passed to ${name}`,
  sandboxOnly:
    "Built from the sandbox's record of this call; the app sent no traces, so the Traces tab is empty.",
  returned: (name) => `${name} returned`,
  noReturn: (name) => `No return from ${name} before the call ended`,
  failed: "error",
  input: "Input",
  output: "Output",
  showDetail: "Input and output",
  showMore: "Show more",
  showLess: "Show less",
};

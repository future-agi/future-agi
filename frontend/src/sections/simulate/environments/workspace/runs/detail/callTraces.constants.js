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
  topology:
    "Shown for one coordinator handing work to its workers one at a time, one level deep.",
  request: "Caller's request",
  response: "Coordinator's reply",
  delegated: (name) => `Delegated to ${name}`,
  returned: (name) => `${name} returned`,
  failed: "error",
  input: "Input",
  output: "Output",
  showDetail: "Input and output",
};

export const CALL_TRACES_COPY = {
  tab: "Traces",
  empty: "No trace for this call",
  error: "Couldn't load this call's traces.",
  noRoot: "Root span not stored yet",
};

export const CALL_WORKERS_COPY = {
  tab: "Workers",
  none: "No worker record for this call: the app sent no traces, or they are still arriving.",
  error: "Couldn't load this call's workers.",
  coordinator: "Coordinator",
  notObserved: "not observed",
  undeclared: "not declared",
  delegated: (name) => `Delegated to ${name}`,
  returned: (name) => `${name} returned`,
  failed: "error",
};

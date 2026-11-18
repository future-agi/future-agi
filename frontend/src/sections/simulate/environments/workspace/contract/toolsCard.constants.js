export const TOOLS_CARD_COPY = {
  subtitle: "The tools the agent declares, and where each one runs",
  arguments: "Arguments",
  noInputs: "Takes no inputs",
  returns: "Returns",
  returnsNotDeclared: "Not recorded for this environment version",
  changes: "Changes",
  effect: {
    read: "Reads only",
    write: "Can change records",
    unknown: "Not declared",
  },
  refusesWhen: "Refuses when",
  needsFirst: "Needs first",
  resetLabel: "Reset",
  reset: "Reset to the seeded records before each trial",
  notReset: "Not reset by the test world",
  labels: {
    simulated: "Simulated",
    inProcess: "Runs inside the agent",
    unsupported: "Unsupported",
  },
  unsupportedWhy:
    "This tool reaches a system the test world has no copy of, so a test never calls it: unsupported for this controlled test.",
  expand: (name) => `Show the contract of ${name}`,
};

// "refund_amount: number, cents, USD"
export function inputLine(name, type, unit) {
  return [`${name}: ${type || "any"}`, unit].filter(Boolean).join(", ");
}

export function returnLine(name, kind) {
  return kind ? `${name}: ${kind}` : name;
}

export function effectLabel(effect) {
  return TOOLS_CARD_COPY.effect[effect] || TOOLS_CARD_COPY.effect.unknown;
}

// The Tools card's detail block: what each tool takes, what it changes, when it refuses and what happens to it
// between trials, read from the contract.

export const TOOLS_CARD_COPY = {
  inputs: "Inputs",
  noInputs: "Takes no inputs",
  changesRecords: "Changes records",
  effect: {
    read: "Reads only",
    write: "Can change records",
    unknown: "Not declared",
  },
  refusesWhen: "Refuses when",
  needsFirst: "Needs first",
  betweenTrials: "Between trials",
  reset: "Reset to the seeded records before each trial",
  notReset: "Not reset by the test world",
  labels: {
    simulated: "Simulated",
    inProcess: "Runs inside the agent",
  },
  expand: (name) => `Show the contract of ${name}`,
};

// One argument as a line: "refund_amount: number, cents, USD".
export function inputLine(name, type, unit) {
  return [`${name}: ${type || "any"}`, unit].filter(Boolean).join(", ");
}

// What a tool changes, from its entrypoint's declared effect.
export function effectLabel(effect) {
  return TOOLS_CARD_COPY.effect[effect] || TOOLS_CARD_COPY.effect.unknown;
}

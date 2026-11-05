export const TRIAL_CHANGES_COPY = {
  tab: "World changes",
  heading: "What changed in the world",
  about:
    "Each participant's attempts in this trial and what the world made of them. Refused and faulted attempts changed nothing.",
  counts: (one) =>
    [
      `${one.attempted} attempted`,
      `${one.committed} committed`,
      one.refused ? `${one.refused} refused` : null,
      one.faulted ? `${one.faulted} faulted` : null,
      one.duplicate ? `${one.duplicate} duplicate` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  committed: (actions) => `Committed: ${actions.join(", ")}`,
  // A worker the world told apart from the rest of the agent.
  worker: (name) => `Worker · ${name}`,
  refusedHeading: "Refused attempts",
  refused: (one) => [one.action, one.reason].filter(Boolean).join(": "),
  decisionsHeading: "What the actors chose",
  plays: {
    mocked: "As scheduled",
    live: "Decided",
    history: "Before the run",
  },
  nothing: "Did nothing",
  empty: "Nothing changed in the world in this trial.",
};

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
  decisionsHeading: "What the actors chose",
  plays: {
    mocked: "As scheduled",
    live: "Decided",
    history: "Before the run",
    waiting: "Waiting for a response",
    timeout: "Timed out",
  },
  // What the world waits for and, once the wait runs out, the configured outcome; nothing is said in the actor's name.
  waited: (one, name) => {
    const then =
      one.fallback === "silent"
        ? "then nothing happens"
        : one.fallback === "decline"
          ? "then the request is declined"
          : `then ${name(one.fallback)} answers instead`;
    return `up to ${one.timeout_seconds ?? 0} s, ${then}`;
  },
  outcomes: {
    timed_out: "No answer: nothing happened",
    declined: "No answer: the request was declined",
    answered_by: (who) => `No answer: ${who} answered instead`,
  },
  nothing: "Did nothing",
  empty: "Nothing changed in the world in this trial.",
};

// Copy for the declared workers, on the Contract tab's Workers card and the agent's Participants card.
export const WORKERS_COPY = {
  title: "Workers",
  subtitle: "The agents this app hands work to, as its source declares them",
  count: (n) => `${n} worker${n === 1 ? "" : "s"}`,
  binding: {
    separate: "Own channel",
    shared: "Shared channel",
    not_declared: "Channel not declared",
  },
  ownProcess: "Own process",
  footer:
    "Declared by the app's source. A run shows which of them actually took part.",
};

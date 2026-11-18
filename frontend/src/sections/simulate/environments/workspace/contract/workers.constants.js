// Copy for the declared workers, on the Contract tab's and the agent's Participants cards.
export const WORKERS_COPY = {
  title: "Participants",
  subtitle: "The agents this app hands work to, as its source declares them",
  count: (n) => `${n} participant${n === 1 ? "" : "s"}`,
  binding: {
    separate: "Own connection",
    shared: "Shares the app's connection",
    not_declared: "Connection not declared",
  },
  ownProcess: "Runs on its own",
  footer:
    "Declared by the app's source. A run shows which of them actually took part.",
};

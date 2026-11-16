// Copy for the Workers card on the Contract tab.
export const WORKERS_COPY = {
  title: "Workers",
  subtitle: "The agents this app hands work to, as its source declares them",
  count: (n) => `${n} worker${n === 1 ? "" : "s"}`,
  binding: {
    separate: "Own channel",
    shared: "Shared channel",
  },
  ownProcess: "Own process",
  mayUse: (tools) => `May use: ${tools.join(", ")}`,
  footer:
    "Declared by the app's source. A run shows which of them actually took part.",
};

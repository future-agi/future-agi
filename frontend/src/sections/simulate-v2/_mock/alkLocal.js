/*
  Running an agent's environment on your own machine with ALK, the
  open-source Agent Learning Kit (plan alk.md, T12, T37). The commands are
  ALK's own quickstart: install, the offline doctor, then the harness, which
  reads the agent's code, builds its world and scenarios and runs them.

  What it contacts is said, not implied (alk.md "Documentation must say when
  a function contacts a provider or platform service"): the model provider
  the harness uses, and Future AGI only when it is connected. Cloud features
  are not promised locally.
*/

/** The copy-paste block: install, check, build and run for one agent folder. */
export const alkCommandsOf = ({ path = "./my-agent", count = 10 } = {}) =>
  [
    "pip install agent-learning-kit",
    "agent-learn doctor",
    `agent-learn harness auto --path ${path} --count ${count}`,
  ].join("\n");

/** What a local run reaches over the network. */
export const ALK_NETWORK =
  "Calls only your model provider. Results reach Future AGI only if you connect it.";

/** What stays in the cloud product. */
export const ALK_CLOUD_ONLY =
  "Improvements, Insights and shared run history stay here.";

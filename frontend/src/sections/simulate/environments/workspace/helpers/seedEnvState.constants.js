// Rendered copy for the seeded and forked version history. Kept out of the
// helper modules so no user-facing string is inlined at a call site.
export const SEED_COPY = {
  templateBaselineName: "Template baseline",
  templateAgentNote: "Shipped with the template.",
  templateEnvNote: "First build from the template.",
  agentVersionNote: "First version connected to this environment.",
  agentEnvNote: "First build from the agent.",
  forkNameSuffix: " · fork",
  forkEnvNote: (name) => `Forked from ${name}.`,
};

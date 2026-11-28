import { isTestedByEnvironment } from "../connectedAgentLabels";

// The server keeps an agent anything still tests, even what its tab doesn't list; the
// reason then asks for no deletes the page can't show.
export const deleteBlockedReason = (agent) => {
  if (!isTestedByEnvironment(agent)) return null;
  const n = agent.environmentCount;
  if (!n) return "Still tested by an environment, so it can't be deleted.";
  return `Tested by ${n} environment${n === 1 ? "" : "s"}. Delete those environments first.`;
};

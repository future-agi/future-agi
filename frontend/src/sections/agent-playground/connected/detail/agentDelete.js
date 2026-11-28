export const deleteBlockedReason = (agent) => {
  if (!agent.environmentCount) return null;
  const n = agent.environmentCount;
  return `Tested by ${n} environment${n === 1 ? "" : "s"}. Delete those environments first.`;
};

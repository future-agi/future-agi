/**
 * The loaded rows that are trials of the open call's scenario, in trial order.
 * Only rows the run table has loaded are known; a scenario's other trials on
 * another page are not listed.
 */
export const trialsOf = (rows, task) => {
  if (!task) return [];
  const scenarioOf = (one) => one.sourceScenarioKey ?? one.sourceScenario;
  const scenario = scenarioOf(task);
  if (scenario == null) return [];
  return (rows || [])
    .filter((one) => scenarioOf(one) === scenario && one.trialIndex != null)
    .sort((a, b) => a.trialIndex - b.trialIndex)
    .map(({ id, trialIndex, status }) => ({ id, trialIndex, status }));
};

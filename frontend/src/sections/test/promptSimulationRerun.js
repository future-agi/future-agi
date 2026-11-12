import { SourceType } from "src/sections/scenarios/common";

// Re-run starts no calls for a prompt simulation in this release; a new run of
// the simulation does. Each Re-run control on a prompt simulation stays on
// screen, turned off, with one of these lines as its tooltip and as what a
// screen reader reads for it.
export const PROMPT_RERUN_NO_CALLS =
  "Re-run does not start calls for prompt simulations in this release.";

// For a page that also has the button that runs the simulation.
export const PROMPT_RERUN_NO_CALLS_RUN_AGAIN = `${PROMPT_RERUN_NO_CALLS} Run the simulation again to get new calls.`;

// True only when a run test's details say it is prompt-based. A run test whose
// details have not loaded keeps its controls as they are.
export const isPromptSimulation = (testData) => {
  const sourceType = testData?.source_type ?? testData?.sourceType;
  return Boolean(sourceType) && sourceType === SourceType.PROMPT;
};

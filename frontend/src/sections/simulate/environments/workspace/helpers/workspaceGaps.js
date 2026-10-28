import { setupGaps } from "src/api/simulate-environments/_fixtures/setupGaps";
import { GAP_AREA_TO_TAB } from "../workspace.constants";

// Bucket the environment's blocking setup gaps by the tab that owns their
// answer. Only blocking gaps badge a tab — an assumed gap does not stop a run,
// so it is not something the reader must resolve before running. The designer's
// Sandbox/Tools areas point at an Agents tab we do not render, so GAP_AREA_TO_TAB
// drops them and only Contract/Grading gaps ever land here.
export function gapsByTab(env, envState) {
  const byTab = {};
  setupGaps(env, envState).forEach((gap) => {
    if (gap.status !== "blocking") return;
    const tabId = GAP_AREA_TO_TAB[gap.area];
    if (!tabId) return;
    (byTab[tabId] = byTab[tabId] || []).push(gap);
  });
  return byTab;
}

// The numeric badges the rail shows next to a tab label. Runs is resolved from
// the live executions hook inside WorkspacePanels (envState.runs is empty for a
// harness env), so it is intentionally left out of this client-state count.
export function counts(envState) {
  return {
    scenarios: envState?.scenarios?.length ?? 0,
    evals: envState?.evals?.length ?? 0,
  };
}

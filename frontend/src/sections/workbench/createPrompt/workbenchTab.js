// The prompt workbench tabs this module decides between (the ?tab= values).
export const WORKBENCH_TAB = {
  PLAYGROUND: "Playground",
  SIMULATION: "Simulation",
};

// An org restricted to Environments has no Simulation tab, so a
// ?tab=Simulation link opens Playground instead.
export function visibleWorkbenchTab(tab, restrictSimulateToEnvironments) {
  return restrictSimulateToEnvironments && tab === WORKBENCH_TAB.SIMULATION
    ? WORKBENCH_TAB.PLAYGROUND
    : tab;
}

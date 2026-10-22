/**
 * Labels for the primary/compare pane tabs in Observe.
 *
 * Graph view renders PrimaryGraph above the grids, so "Graph" is accurate there.
 * Table / agent views render TraceGrid content under these tabs, so "Data" matches.
 */
export function getObservePaneTabLabel(viewMode, pane) {
  const noun = viewMode === "graph" ? "Graph" : "Data";
  return pane === "compare" ? `Comparison ${noun}` : `Primary ${noun}`;
}

/**
 * Resolve historical/surface-specific view modes into a supported topology.
 *
 * `agentPath` inferred sequence from parentage and produced misleading paths,
 * so legacy URLs and saved views now open the truthful Agent Graph instead.
 * Simulator projects support only the standard graph.
 */
export const canonicalObserveViewMode = ({ viewMode, isSimulator }) => {
  if (isSimulator) return "graph";
  return viewMode === "agentPath" ? "agentGraph" : viewMode;
};

/**
 * Resolve historical/surface-specific view modes into a supported topology.
 *
 * Simulator projects support only the standard graph. Cross-project user
 * detail also needs one selected project before either topology is meaningful.
 */
export const canonicalObserveViewMode = ({
  viewMode,
  isSimulator,
  agentGraphEnabled = true,
}) => {
  if (isSimulator || !agentGraphEnabled) return "graph";
  return viewMode;
};

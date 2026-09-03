import { PROJECT_SOURCE } from "src/utils/constants";

/**
 * Trace/span lists are valid for every resolved non-simulator project source.
 * Backend projects include legacy `demo` values, and future sources must not
 * silently turn into a client-side successful empty list.
 */
export const isTraceListProjectReady = ({
  projectId,
  projectSource,
  allowOrgScope = false,
}) =>
  (Boolean(projectId) || allowOrgScope) &&
  Boolean(projectSource) &&
  projectSource !== PROJECT_SOURCE.SIMULATOR;

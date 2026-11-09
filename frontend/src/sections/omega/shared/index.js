// Shared Omega data layer for the Future AGI tabs (Deployments, Sandboxes,
// Console frame). Owned by lane `platform`; tabs import from here instead of
// copying transport code.
export { OMEGA_STATE, OmegaError, isSignInUnconfirmed } from "./omegaStates";
export {
  REFUSAL_COPY,
  customerCopy,
  isCustomerSafe,
  refusalEntry,
} from "./omegaReasons";
export {
  createAssertionMinter,
  OMEGA_AUDIENCE,
  SESSION_EXCHANGE_PATH,
} from "./sessionAssertion";
export {
  createOmegaClient,
  omegaFetch,
  omegaJson,
  DEFAULT_OMEGA_API_BASE,
  MALFORMED_RESPONSE,
  requireList,
} from "./omegaClient";
export { openOmegaStream, createSseParser } from "./omegaStream";
export { STREAM_MODE, currentStreamMode, resetStreamMode } from "./streamMode";
export { useOmegaSession, probeOmegaSession } from "./useOmegaSession";
export {
  OBSERVE_LINK_FILTER_PARAM,
  OBSERVE_DATE_FILTER_PARAM,
  OBSERVE_ORIGIN_PARAM,
  OBSERVE_RETURN_PARAM,
  OBSERVE_TRACE_TAB,
  OBSERVE_SELECTED_TAB,
  deploymentObserveFilters,
  buildObserveLink,
  buildObserveTraceLink,
  buildObserveErrorLink,
  buildObserveRunLink,
  buildAddEvalsLink,
  buildObserveIssueLink,
  parseObserveOrigin,
  observeLinkForTarget,
  observeLinkContract,
} from "./observeLinks";
export {
  OBSERVE_DEPLOYMENTS_ROOT,
  observeRouteTemplates,
  observeRoutes,
  createObserveClient,
  ensureObserveTarget,
  linkDeploymentToObserve,
} from "./observeClient";

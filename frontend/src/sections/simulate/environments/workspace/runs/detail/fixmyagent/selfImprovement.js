import { useRestrictSimulateToEnvironments } from "src/hooks/useRestrictSimulateToEnvironments";

/**
 * Self-improvement (the prompt optimizer launched from a run) is in beta: its
 * entry points stay visible but cannot be used. This is the one switch; a real
 * beta flag plugs in here.
 */
export const SELF_IMPROVEMENT_BETA_OPEN = false;

/**
 * Orgs restricted to Environments (TH-8602) stay closed even once the beta
 * opens: an optimization's results open under Run Simulation, which those orgs
 * get a 404 for. Lift this once the results have a page under Environments.
 */
export const selfImprovementOpen = ({ betaOpen, restricted }) =>
  betaOpen && !restricted;

export function useSelfImprovementOpen() {
  const restricted = useRestrictSimulateToEnvironments();
  return selfImprovementOpen({
    betaOpen: SELF_IMPROVEMENT_BETA_OPEN,
    restricted,
  });
}

export const SELF_IMPROVEMENT_BETA_HINT = "In beta, send early access request";

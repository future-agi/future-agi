import { useAuthContext } from "src/auth/hooks";
import { paths } from "src/routes/paths";

/**
 * True for an org created after the Simulate cutoff: it sees only Environments,
 * not Agent Definition, Scenarios, Personas, Run Simulation, the prompt
 * Simulation tab or the Observe replay actions. user-info carries the flag.
 */
export function useRestrictSimulateToEnvironments() {
  const { user } = useAuthContext();
  return Boolean(user?.restrict_simulate_to_environments);
}

// The pages the router does not register for a restricted org. The Agent
// Definition list itself redirects to Connected agents for every org.
const HIDDEN_PAGES = [
  `${paths.dashboard.simulate.agentDefinition}/`,
  paths.dashboard.simulate.scenarios,
  paths.dashboard.simulate.test,
];

/** Whether an in-app path leads to a page a restricted org gets the 404 for. */
export function isHiddenFromRestrictedOrgs(path) {
  const pathname = String(path ?? "").split(/[?#]/)[0];
  return HIDDEN_PAGES.some((page) =>
    page.endsWith("/")
      ? pathname.startsWith(page)
      : pathname === page || pathname.startsWith(`${page}/`),
  );
}

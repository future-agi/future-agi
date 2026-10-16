import { useAuthContext } from "src/auth/hooks";

/**
 * True for an org created after the Simulate cutoff: it sees only Environments,
 * not Agent Definition, Scenarios, Personas, Run Simulation, the prompt
 * Simulation tab or the Observe replay actions. user-info carries the flag.
 */
export function useRestrictSimulateToEnvironments() {
  const { user } = useAuthContext();
  return Boolean(user?.restrict_simulate_to_environments);
}

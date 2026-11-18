import { REPLAY_ACTION_ID } from "./constants";

// Replay creates an agent definition, scenarios and a Run Simulation run, all
// hidden from an org restricted to Environments, so it has no replay action.
export function withoutReplayForRestrictedOrgs(
  actions,
  restrictSimulateToEnvironments,
) {
  return restrictSimulateToEnvironments
    ? actions.filter((action) => action.id !== REPLAY_ACTION_ID)
    : actions;
}

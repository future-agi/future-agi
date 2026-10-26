import { ENV_STATUS } from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "./workspace.constants";

// The named reason a run is blocked, first match wins: a run already starting,
// a built environment that isn't Ready, no agent (client-only environments),
// no scenarios, a built environment with no linked run, then a generic
// fallback. Only read when Run is disabled, so it never shows for a runnable
// environment. Evals don't block a run; a run without them just isn't scored.
export const blockedReason = (envState, env, backed, starting = false) => {
  if (starting) return WORKSPACE_COPY.runBlocked.starting;
  // A backed env runs only once its job stage is `completed` (Ready), the
  // same check the run endpoint makes. A run is its own child job, so a run in
  // progress never changes this.
  if (backed && env?.status && env.status !== ENV_STATUS.READY) {
    return WORKSPACE_COPY.runBlocked.notReady;
  }
  // A built env's agent is filled in by the detail mapper whatever the API
  // sent, so "no agent" can only be true of a client-only env.
  if (!backed && !envState.agent) return WORKSPACE_COPY.runBlocked.agent;
  if (!envState.scenarios?.length) return WORKSPACE_COPY.runBlocked.scenarios;
  // A backed env runs through its run test; until the build links one, there
  // is nothing to start a run on.
  if (backed && !env?.platform?.runTestId) return WORKSPACE_COPY.runBlocked.noRunTest;
  return WORKSPACE_COPY.runBlocked.generic;
};

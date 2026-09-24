import { useCallback, useEffect, useRef } from "react";
import { useEnvironmentsStore } from "./useEnvironmentsStore";

// The per-environment slice shape. The designer's prototype carried extra
// twin/releases/optimization fields; those surfaces are out of scope here, so
// this is the trimmed set the workspace actually reads.
export const emptyEnvState = () => ({
  agent: null,
  additionalAgents: [],
  activeAgentId: null,
  scenarios: [],
  scenarioSource: null,
  evals: [],
  runs: [],
  agentVersions: [],
  envVersions: [],
  activeEnvVersion: null,
  activeAgentVersion: null,
  seededFromTemplate: false,
  gapsResolved: {},
  baselineRunId: null,
});

// One environment's slice plus the actions bound to its id. `bootstrap` is the
// harness-derived initial state used when the store has never seen this env: it
// is surfaced immediately and written into the store once, so a refresh or a
// direct navigation to an env that only exists in the harness backend still has
// a usable state. Adopted (build/template) envs already carry a slice, so they
// never take the bootstrap path.
//
// `persist` gates that write. A job that is still building re-derives its
// bootstrap on every poll, so persisting the first one froze the environment at
// whatever the run had produced by then — the scenarios that landed later never
// reached the workspace. With `persist: false` the bootstrap is surfaced live
// and only written once the build is terminal, after which user edits own the
// slice as before.
export function useEnvState(envId, bootstrap, { persist = true } = {}) {
  const slice = useEnvironmentsStore((s) => s.byEnv[envId]);
  const patchEnvState = useEnvironmentsStore((s) => s.patchEnvState);
  const recordRunAction = useEnvironmentsStore((s) => s.recordRun);
  const addAgentVersionAction = useEnvironmentsStore((s) => s.addAgentVersion);

  // Keyed by env id, not a boolean: the workspace keeps one component instance
  // across a fork navigation (env → env-fork), so a boolean guard would refuse
  // to bootstrap the second env.
  const bootstrappedRef = useRef(null);
  useEffect(() => {
    if (slice || !bootstrap || !persist || bootstrappedRef.current === envId) return;
    bootstrappedRef.current = envId;
    patchEnvState(envId, bootstrap);
  }, [slice, bootstrap, persist, envId, patchEnvState]);

  // Surface a full-shape state on the first render too: patchEnvState merges the
  // bootstrap into emptyEnvState(), so returning the merged shape keeps the
  // state (and canRun) stable across the write.
  const envState =
    slice || (bootstrap ? { ...emptyEnvState(), ...bootstrap } : emptyEnvState());

  const patch = useCallback(
    (p) => patchEnvState(envId, p),
    [patchEnvState, envId],
  );
  const recordRun = useCallback(
    (run) => recordRunAction(envId, run),
    [recordRunAction, envId],
  );
  const addAgentVersion = useCallback(
    (version) => addAgentVersionAction(envId, version),
    [addAgentVersionAction, envId],
  );

  const canRun = !!envState.agent && (envState.scenarios?.length || 0) > 0;

  return { envState, patch, recordRun, addAgentVersion, canRun };
}

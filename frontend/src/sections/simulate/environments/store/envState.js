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
  // Per-tool read/write override keyed by tool name, set from the Contract
  // tab's effect picker. Wins over the job's real classification (and over the
  // verb-heuristic fallback) when present.
  toolResolutions: {},
});

// One environment's slice plus the actions bound to its id. `bootstrap` is the
// harness-derived initial state used when the store has never seen this env: it
// is surfaced immediately and written into the store once, so a refresh or a
// direct navigation to an env that only exists in the harness backend still has
// a usable state. Adopted (build/template) envs already carry a slice, so they
// never take the bootstrap path.
export function useEnvState(envId, bootstrap) {
  const slice = useEnvironmentsStore((s) => s.byEnv[envId]);
  const patchEnvState = useEnvironmentsStore((s) => s.patchEnvState);
  const recordRunAction = useEnvironmentsStore((s) => s.recordRun);
  const addAgentVersionAction = useEnvironmentsStore((s) => s.addAgentVersion);

  // Keyed by env id, not a boolean: the workspace keeps one component instance
  // across a fork navigation (env → env-fork), so a boolean guard would refuse
  // to bootstrap the second env.
  const bootstrappedRef = useRef(null);
  useEffect(() => {
    if (slice || !bootstrap || bootstrappedRef.current === envId) return;
    bootstrappedRef.current = envId;
    patchEnvState(envId, bootstrap);
  }, [slice, bootstrap, envId, patchEnvState]);

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

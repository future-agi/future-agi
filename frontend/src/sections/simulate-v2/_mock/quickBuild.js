/*
  Finishing a build someone left part-way (opening the environment completes
  it). No run is ever recorded here — a first run is started by the person. Fix no longer builds an
  environment itself — it hands over to the normal builder and comes back
  (errorFeed/IssueReturnBanner.jsx). Everything here is pure: it returns the
  records and patches, and the caller dispatches them.
*/
import { generatedPool } from "./scenarios";
import { currentAgentVersion } from "./versions";
import { stampProvenance, currentUser } from "./scenarioProvenance";

/* The builder dialog's default size. */
export const QUICK_SCENARIOS = 10;
export const QUICK_REPEATS = 3;

/* Fit the derived pool to a count — the builder page's sizeToCount. Past the
   pool's size the same situations recur with another caller persona. */
const sizeToCount = (pool, count) => {
  if (!count || !pool.length) return pool;
  if (count <= pool.length) return pool.slice(0, count);
  return Array.from({ length: count }, (_, i) => {
    const base = pool[i % pool.length];
    if (i < pool.length) return base;
    const round = Math.floor(i / pool.length);
    const persona = pool[(i * 7 + round) % pool.length]?.persona || base.persona;
    return { ...base, id: `${base.id}~${round + 1}`, persona };
  });
};

/**
 * Finish a build that stopped part-way. A build runs on the builder page; if
 * someone leaves before its last stage, the environment would otherwise stay
 * "Building" forever. Opening it finishes what's missing — the scenarios the
 * build was asked for, its v1, its evaluations — and stamps it ready. Pure:
 * returns { envPatch, statePatch }, or null when there's nothing to finish.
 */
export const finishBuildPatch = (env, envState = {}, now = new Date()) => {
  if (env?.buildStatus !== "building") return null;
  const at = now.toISOString();
  const kept = (envState.scenarios || []).filter((r) => !r.proposed && !r.archived);
  /* Built from scratch: the scenarios the approved design proposed from the
     description, not a pool read from an agent (builder.md §3–4). */
  const scratch = env.builtFrom?.kind === "scratch";
  const pool = scratch && env.starterScenarios?.length ? env.starterScenarios : generatedPool(env);
  const scenarios = kept.length
    ? envState.scenarios
    : [
      ...(envState.scenarios || []),
      ...sizeToCount(pool, env.builtFrom?.scenarioCount || QUICK_SCENARIOS)
        .map((r) => stampProvenance(r, { source: "derived", actor: currentUser(), at, batchId: `batch_build_${env.id}` })),
    ];
  const count = scenarios.filter((r) => !r.proposed && !r.archived).length;
  const versions = envState.envVersions || [];
  const builtFor = scratch ? null : currentAgentVersion(envState)?.label || "v1";
  const envVersions = versions.length
    /* Still the first build: v1 carries what the build made. */
    ? versions.map((v, i) => (i === 0 && versions.length === 1 ? { ...v, scenarios: count } : v))
    : [{ id: `${env.id}-v1`, label: "v1", createdAt: at, note: scratch ? "First build, from your description." : `First build, read from agent ${builtFor}.`, scenarios: count, changed: ["contract", "seed"], tools: (env.tools || []).map((t) => ({ ...t })), builtFor }];
  const total = env.buildProgress?.total || 3;
  return {
    envPatch: { buildStatus: "ready", buildProgress: { done: total, total } },
    statePatch: {
      scenarios,
      envVersions,
      ...(!envState.activeEnvVersion && { activeEnvVersion: "v1" }),
    },
  };
};

import { emptyEnvState } from "../../store/envState";
import { SEED_COPY } from "./seedEnvState.constants";

// Pure clone of an environment: mints a fresh env instance carrying the same
// world (tools, rules, scenarios, evals, seed) but with no run history and its
// own v1 lineage. The store's forkEnvironment action writes this record verbatim
// and does not stamp `adoptedAt`, so the returned env carries it directly.
// Returns `{ env, envState }` for the store to register.
export function forkEnvironment(env, envState, now) {
  const suffix = Math.random().toString(36).slice(2, 8);
  const forkedId = `${env.id}-fork-${suffix}`;
  const scenarios = [...(envState.scenarios || [])];
  return {
    env: {
      ...env,
      id: forkedId,
      name: `${env.name}${SEED_COPY.forkNameSuffix}`,
      custom: true,
      forkedFrom: env.id,
      buildProgress: undefined,
      // The parent's run links must not come along: `platform` is how the Runs
      // tab and the header's Run action locate an environment's executions, so a
      // copied one opens a brand-new fork on the parent's run history and makes
      // "Run again" re-run the parent.
      platform: undefined,
      adoptedAt: now,
    },
    // A fork inherits the world but becomes editable: the template sticker is
    // peeled off, the version lineage restarts at v1 against the seeded agent,
    // and there is no run history.
    envState: {
      ...emptyEnvState(),
      scenarios,
      evals: [...(envState.evals || [])],
      scenarioSource: envState.scenarioSource,
      agent: envState.agent,
      additionalAgents: [...(envState.additionalAgents || [])],
      activeAgentId: envState.activeAgentId ?? null,
      agentVersions: envState.agentVersions
        ? envState.agentVersions.map((v) => ({ ...v }))
        : [],
      envVersions: [
        {
          id: `${forkedId}-v1`,
          label: "v1",
          createdAt: now,
          note: SEED_COPY.forkEnvNote(env.name),
          scenarios: scenarios.length,
          changed: ["fork"],
        },
      ],
      envDerivedForAgent: "v1",
      activeAgentVersion: "v1",
      seededFromTemplate: false,
      runs: [],
    },
  };
}

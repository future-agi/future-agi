import { emptyEnvState } from "../../store/envState";
import { SEED_COPY } from "./seedEnvState.constants";

// Pure clone of an environment: mints a fresh env instance carrying the same
// world (tools, rules, scenarios, evals, seed) but with no run history and its
// own v1 lineage. The store's forkEnvironment action writes this record verbatim
// and does not stamp `adoptedAt`, so the returned env carries it directly.
// Returns `{ env, envState }` for the store to register.
//
// The env record is assembled from an explicit field list, never `{ ...env }`.
// A parent may carry a bridge into the product run (`platform.runTestId` /
// `platform.testExecutionId`) and its own build state (`buildStatus`,
// `buildProgress`, `stageOutputs`). Spreading those onto the fork made the
// fork's Runs tab list the parent's executions, made Run open the parent's
// execution, and left a fork of a still-building parent reading "building"
// forever. A fork is a fresh environment with no run of its own, so it carries
// the world and nothing else.
export function forkEnvironment(env, envState, now) {
  const suffix = Math.random().toString(36).slice(2, 8);
  const forkedId = `${env.id}-fork-${suffix}`;
  const scenarios = [...(envState.scenarios || [])];
  return {
    env: {
      id: forkedId,
      name: `${env.name}${SEED_COPY.forkNameSuffix}`,
      surface: env.surface,
      agentType: env.agentType,
      domain: env.domain,
      tagline: env.tagline,
      description: env.description,
      difficulty: env.difficulty,
      tools: env.tools,
      rules: env.rules,
      seed: env.seed,
      evalPreset: env.evalPreset,
      custom: true,
      forkedFrom: env.id,
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

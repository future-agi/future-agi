/*
  An agent's constraints — what an improvement may change and what it must
  not move. Kept per agent, so every improvement for it (from the Error Feed,
  from Debug failures, from a goal) is held to the same rules.
*/
import { useCallback, useMemo } from "react";
import { useSimStore } from "../store";
import { DEFAULT_LIMITS, PER_CONVERSATION } from "../_mock/optimizationRuns";
import { definitionOf, sourceKindOf } from "../_mock/agentRegistry";
import { limitsOf } from "./limitsModel";

/* The pure readings live in limitsModel.js, so the scheduler can use them
   without the store; re-exported here for every screen that reads them. */
export { DEFAULT_SCHEDULE, limitsOf, SCHEDULE_DAYS, nextScheduledAt, scheduleTimeLabel } from "./limitsModel";

/*
  A canary needs the platform to send a share of calls to the draft while the
  rest stay on today's version. Vapi and Retell can. A person starts it from
  the improvement — automatic runs stop at drafts.
*/
export const canarySupported = (agent) => ["vapi", "retell"].includes(agent?.values?.provider);

/** The newest improvement a schedule started for this agent, in any of its environments. */
export const lastAutomaticRun = (state, agent) => (agent?.usedIn || [])
  .flatMap((u) => (state?.byEnv?.[u.envId]?.optimizations || [])
    .filter((o) => o.trigger?.kind === "schedule")
    .map((record) => ({ envId: u.envId, es: state.byEnv[u.envId], record })))
  .sort((a, b) => (Date.parse(b.record.createdAt || 0) || 0) - (Date.parse(a.record.createdAt || 0) || 0))[0] || null;

/* What a conversation costs today, from the surface of an environment it
   runs in — null until it runs in one. */
export const measuredCostOf = (state, agent) => {
  const surface = (agent?.usedIn || [])
    .map((u) => (state?.myEnvironments || []).find((e) => e.id === u.envId)?.surface)
    .find(Boolean);
  return surface ? PER_CONVERSATION[surface === "voice" ? "voice" : "chat"] : null;
};

/** A repository linked to an agent that isn't read from one — where its code lives. */
export const linkedRepoOf = (agent) => definitionOf(agent).repoUrl || agent?.values?.repoUrl || agent?.values?.linkedRepo || null;

/* Several agents working as one on a platform — a Vapi squad, a Retell
   conversation flow, an ElevenLabs workflow's nodes. */
const hasMembersOrFlows = (agent) => (definitionOf(agent).members || []).length > 0
  || agent?.values?.retellKind === "conversation_flow";

/**
 * Which kinds of change this agent can take at all. Code needs its code (a
 * repository or an upload). Settings need somewhere to change them: a
 * hosted platform's config, our own for an agent built here, or the agent's
 * code. Structure needs the code, or a platform agent made of members or a
 * flow (shipped with a written rationale). Anything else is said, not hidden.
 */
export const surfaceAvailability = (agent) => {
  const kind = sourceKindOf(agent);
  /* Code changes need the code: a repository agent, an upload, a framework
     agent — or a hosted or built-in agent whose tools live in a linked repo. */
  const code = kind === "repo" || kind === "upload" || kind === "framework" || !!linkedRepoOf(agent);
  /* The agent's own code connected — its settings live there too. */
  const codeConnected = kind === "repo" || kind === "upload" || !!linkedRepoOf(agent);
  return {
    prompt: { ok: true },
    tools: { ok: kind !== "endpoint", why: "We can only call this agent, so its tools can't be changed" },
    settings: {
      ok: kind === "platform" || kind === "builtin" || codeConnected,
      why: kind === "framework" ? "Needs the agent's code — connect its repository" : "We can only call this agent, so its settings can't be changed",
    },
    /* Skills: a code agent can gain a new one; others only change skills
       they already have. */
    skills: {
      ok: code || !!agent?.values?.skills?.length,
      why: "This agent has no skills — connect its code to add one",
    },
    memory: {
      ok: kind === "platform" || kind === "builtin" || codeConnected,
      why: "We can only call this agent, so its memory can't be changed",
    },
    code: { ok: code, why: "Needs the agent's code — connect it from a repository" },
    structure: {
      ok: code || (kind === "platform" && hasMembersOrFlows(agent)),
      why: kind === "platform" ? "Needs members or a flow — this agent is a single prompt" : "Needs the agent's code — connect it from a repository",
    },
  };
};

export function useAgentLimits(agentKey) {
  const { state, dispatch } = useSimStore();
  const limits = useMemo(() => limitsOf(state, agentKey), [state, agentKey]);
  const set = useCallback((patch) => dispatch({ type: "setAgentLimits", agentKey, limits: { ...limitsOf(state, agentKey), ...patch } }), [dispatch, state, agentKey]);
  const reset = useCallback(() => dispatch({ type: "setAgentLimits", agentKey, limits: DEFAULT_LIMITS }), [dispatch, agentKey]);
  return { limits, set, reset, custom: !!state?.agentLimits?.[agentKey] };
}

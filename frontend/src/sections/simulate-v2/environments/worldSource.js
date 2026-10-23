/*
  Where an environment's world comes from, when it isn't read from the agent.

  Every environment belongs to exactly one agent in Agents (RSI PRD: "From
  Phase 1, each environment belongs to exactly one agent in Agents"). A
  prebuilt world, an MCP server or a SaaS sandbox is never an environment on
  its own — it's the world source of one agent's build: the agent is picked
  (or connected) first, and the world seeds what the builder makes for it.

  Kept out of the .jsx files so they export components only.
*/
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { buildPresetFor } from "../agents/agentViewUtils";
import { agentStatusOf, isComingSoon } from "../_mock/rsiModel";
import { worldFromTemplate } from "../_mock/agentWorlds";
import { generatedPool } from "../_mock/scenarios";
import { MODALITY_FOR } from "../_mock/fidelity";
import { runtimeTypeFor } from "../_mock/builder";
import { useSimStore } from "../store";
import { answerGapPatch, setupGaps } from "../_mock/setupGaps";

/**
 * The agents an environment can be built for: in Agents (not a template's
 * stand-in or a world's sample agent), connected (not archived or cut off),
 * and not Coming soon.
 */
export const buildableAgentsOf = (agents = []) => agents.filter(
  (a) => !a.placeholder && !a.sample && !isComingSoon(a) && agentStatusOf(a).status === "connected",
);

/** The channel a prebuilt world is for — "voice" or "chat". */
export const worldChannelOf = (template) => (MODALITY_FOR[template?.surface] === "voice" ? "voice" : "chat");

/* A world's sample agent: one per world, in Agents tagged Sample, so every
   environment still belongs to an agent in Agents. It's the agent the world
   was designed and proven with. */
export const sampleAgentOf = (template, now = new Date().toISOString()) => ({
  key: `sample:${template.id}`,
  name: `${template.name} sample agent`,
  typeId: runtimeTypeFor(MODALITY_FOR[template.surface] || "chat", null, null)?.id,
  sourceKind: "builtin",
  values: {},
  via: "sample",
  /* Seeded: the environment's first real agent can still take its place. */
  seeded: true,
  sample: true,
  sampleOf: template.id,
  connectedAt: now,
});

/**
 * Try a prebuilt world now: an environment from the world exactly as
 * designed — its tables, tools, rules, its own proven scenarios and its
 * evaluations — tested with the world's sample agent, ready to run. Nothing
 * to connect, nothing to build. Connecting your agent later puts yours in
 * its place (the environment's first real agent).
 */
export function useTryWorld() {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();
  return useCallback((template) => {
    const now = new Date().toISOString();
    const env = { ...template, id: `${template.id}-${Math.random().toString(36).slice(2, 8)}`, templateId: template.id };
    dispatch({ type: "adoptEnvironment", env, now });
    const scenarios = generatedPool(env);
    const tools = (env.tools || []).map((t) => ({ ...t }));
    const base = {
      envVersions: [{
        id: `${env.id}-v1`, label: "v1", createdAt: now,
        note: `Ready-made from the ${template.name} world, with its sample agent.`,
        scenarios: scenarios.length, changed: ["contract", "seed"], tools, builtFor: "v1",
      }],
      activeEnvVersion: "v1",
      agent: sampleAgentOf(template, now),
      agentVersions: [{ id: "agent-v1", label: "v1", note: "Ships with the world.", reach: "seed", createdAt: now, tools }],
      activeAgentVersion: "v1",
      scenarios,
      scenarioSource: "templates",
      evals: [...(env.evalPreset || [])],
      runs: [],
    };
    /* The world as designed has already answered what a build would ask —
       its open questions start settled with the world's own answers, so it
       runs with nothing waiting on you. */
    const settled = setupGaps(env, base)
      .filter((g) => (g.status === "optional" || g.status === "blocking") && g.suggested != null && g.fixedBy !== "action")
      .reduce((st, g) => ({ ...st, ...answerGapPatch(g, "assumed", g.suggested, st) }), base);
    dispatch({ type: "patchEnvState", envId: env.id, patch: settled });
    navigate(paths.dashboard.simulate.environmentDetail(env.id));
  }, [dispatch, navigate]);
}

/** The host an MCP server is reached at — or the command, for stdio. */
export const mcpHostOf = (value = "") => {
  const first = String(value).trim().split(/\s+/)[0];
  try { return new URL(first).hostname; } catch { return first; }
};

/** A prebuilt world as the world an agent's environment starts from. */
export const templateWorldSource = (template) => ({
  kind: "template",
  id: template.id,
  label: template.name,
  world: worldFromTemplate(template),
});

/** An MCP server as the world an agent's environment starts from. */
export const mcpWorldSource = ({ transport, value, tokenRef, gateway, label }) => ({
  kind: "mcp",
  label: label || mcpHostOf(value),
  transport,
  value,
  ...(tokenRef && { tokenRef }),
  /* A server registered in the Gateway: its tools come from there, credentials and all. */
  ...(gateway && { gateway }),
});

/**
 * Build an environment for an agent, as the agent page does — the name,
 * version and size picked in BuildEnvironmentDialog — optionally starting
 * from a world source.
 */
export function useBuildForAgent() {
  const navigate = useNavigate();
  return useCallback((agent, choice, worldSource = null) => navigate(paths.dashboard.simulate.environmentBuild, {
    state: { presetSource: { ...buildPresetFor(agent, choice), ...(worldSource && { worldSource }) } },
  }), [navigate]);
}

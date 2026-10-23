/*
  Pointing an environment at another agent (PRD v7 §4.10.1, AC-23.4).

  The agent is only a reference. Pointing the environment at a different
  agent is a new AGENT version for this environment's subject — never a new
  environment or scenario-set version. Everything it no longer fits is
  listed as nudges (Add / Remove), and a different channel or runtime
  proposes forking a new environment (a core change, P21). Nothing in E or S
  moves until someone approves one.

  Connecting the first agent isn't a switch — there's nothing to compare with.
*/
import { agentVersions, currentAgentVersion, environmentVersions, isEnvDraft, majorOf, nextAgentVersion, nextEnvVersion } from "./versions";
import { agentToolsFor } from "./toolFit";
import { detectNudges, withNudges } from "./decoupled";
import { coreNudgeOf } from "./nudgeEffects";
import { getAgentType } from "./agentTypes";
import { definitionOf, sourceKindOf } from "./agentRegistry";
import { agentMcpTools } from "./mcpServers";
import { worldToolsFor } from "./toolFit";

/** Where a connected agent runs: the environment it's used in, else its type's first surface. */
export const surfaceOfAgent = (picked, state) => {
  const u = picked?.usedIn?.[0];
  const e = u && (state?.myEnvironments || []).find((x) => x.id === u.envId);
  return e?.surface || getAgentType(picked?.typeId)?.surfaces?.[0] || null;
};

/**
 * Whether `picked` is the same type of agent as this environment tests — it
 * runs on the environment's channel. A voice environment only ever tests
 * voice agents: another type would break the world, so it's never offered
 * as a replacement (it builds its own environment instead). Unknown = fits.
 */
export const fitsEnvChannel = (picked, env, state) => {
  const want = env?.surface;
  if (!want || !picked) return true;
  /* Channels the agent itself declares (a voice agent built in code). */
  const declared = picked?.values?.channels || picked?.channels;
  if (declared?.length) return declared.includes(want);
  const used = surfaceOfAgent(picked, state);
  const types = getAgentType(picked?.typeId)?.surfaces || [];
  if (types.includes(want)) return true;
  return !used || used === want;
};

/** The tools a connected agent's version calls — read where it already runs, else its record. */
const toolsOfPicked = (picked, label, state) => {
  const u = (picked?.usedIn || []).find((x) => state?.byEnv?.[x.envId]);
  const e = u && (state?.myEnvironments || []).find((x) => x.id === u.envId);
  if (e) return agentToolsFor(e, { ...state.byEnv[u.envId], agentVersions: picked.versions?.length ? picked.versions : state.byEnv[u.envId].agentVersions }, label);
  return (picked?.versions || []).find((v) => v.label === label)?.tools || null;
};

/**
 * The tools an agent calls, read from the agent itself — where it's already
 * tested, else what its source declares (a repository's code, a platform's
 * assistant). An endpoint or a pasted prompt declares none: their tools only
 * show up once a run observes them. → [{ name, desc, args }]
 */
export const readAgentTools = (picked, label, state) => {
  /* Tools its MCP servers list come too — they aren't in the agent's own config. */
  const viaMcp = agentMcpTools(picked);
  const withMcp = (list) => [...list, ...viaMcp.filter((t) => !list.some((x) => x.name === t.name))];
  const tested = toolsOfPicked(picked, label, state);
  /* Whole records: a tool that came from an MCP server keeps where it came from. */
  if (tested?.length) return withMcp(tested.map((t) => ({ ...t, desc: t.desc || "" })));
  const kind = sourceKindOf(picked);
  if (kind === "endpoint" || kind === "prompt") return viaMcp;
  /* Only what the agent's own source or versions declare — never a demo world's
     tools standing in for an agent that declares none. */
  const own = [...(picked?.versions || [])].reverse().find((v) => v.tools?.length)?.tools || definitionOf(picked).tools || [];
  return withMcp(own.map((t) => (typeof t === "string" ? { name: t, desc: "" } : { name: t.name, desc: t.desc || "", ...(t.args?.length && { args: t.args }) })));
};

/**
 * Connecting the first agent to an environment built from a description:
 * the world had no tools to answer, so the agent is read now — the same
 * "read the agent" a build from an agent starts with. Its tools become the
 * world's; a never-run environment (a draft) takes them in place on v1, one
 * that has run gets the next version. Every scenario proved before the read
 * is re-checked: its reference solution was written without the tools.
 *   → { envPatch, statePatch, tools } or null when there's nothing to read
 */
export const firstAgentReadPlan = (env, envState, { picked, label, agentName, state, now = new Date().toISOString() }) => {
  if (envState?.agent || envState?.toolsRead) return null;
  /* A world built from an agent or a prebuilt world already knows its tools;
     one with only tools added from an MCP server still reads the agent. */
  const existing = worldToolsFor(env, envState);
  if (existing.some((t) => t.via?.kind !== "mcp")) return null;
  const fromAgent = readAgentTools(picked, label, state);
  if (!fromAgent.length) return null;
  /* A tool the world already took from an MCP server stays that record — its
     server, read-only flag and Gateway standing — when the agent declares it too. */
  const kept = new Set(existing.map((t) => t.name));
  const tools = [...existing, ...fromAgent.filter((t) => !kept.has(t.name))];
  const note = `Tools read from ${agentName}${label ? ` ${label}` : ""} when it was connected.`;
  const read = { from: agentName, label: label || null, at: now, count: fromAgent.length, names: fromAgent.map((t) => t.name) };
  if (isEnvDraft(envState)) {
    const v1 = [...environmentVersions(env, envState)].reverse()[0];
    return {
      tools, fromAgent,
      envPatch: { tools, firstBuildTools: tools },
      statePatch: { toolsRead: read, envVersions: [{ ...(({ current, ...rest }) => rest)(v1), tools, note: `First build, from your description. ${note}`, changed: [...new Set([...(v1.changed || []), "contract"])] }] },
    };
  }
  const minted = nextEnvVersion(env, envState, { changed: ["contract"], note, now, tools });
  return {
    tools, fromAgent,
    /* Unsaved edits ride along into the version — said, never silent. */
    savedEdits: (envState?.unsavedEnvChanges || []).length,
    /* Versions before this one keep the world they had: the first build's list is frozen as it was. */
    envPatch: { tools, firstBuildTools: env?.firstBuildTools || env?.tools || [] },
    statePatch: { toolsRead: read, envVersions: [...(envState.envVersions?.length ? envState.envVersions : [...environmentVersions(env, envState)].reverse()), minted], activeEnvVersion: minted.label, unsavedEnvChanges: [] },
  };
};

/**
 * What switching to `picked` (at version `label`) would do here: the agent
 * version it becomes and the nudges it would raise. Null for a first connect.
 *   { version, prev, nudges, core }
 */
export const agentSwitchPlan = (env, envState, { picked, label, state }) => {
  if (!envState?.agent || !picked) return null;
  const list = agentVersions(envState);
  const n = Math.max(0, ...list.map((v) => majorOf(v.label))) + 1;
  const prev = currentAgentVersion(envState)?.label || list[list.length - 1]?.label || null;
  const tools = toolsOfPicked(picked, label, state);
  const version = {
    ...nextAgentVersion(envState, { note: `Pointed at ${picked.name}${label ? ` ${label}` : ""}.`, tools, basedOnVersion: prev }),
    id: `agent-v${n}`,
    label: `v${n}`,
    origin: "switch",
    switchedTo: { key: picked.key || null, name: picked.name, label: label || null },
  };
  const after = { ...envState, agentVersions: [...list, version] };
  const { nudges } = detectNudges(env, after, version.label, prev);
  const core = coreNudgeOf(env, after, { newLabel: version.label, prevLabel: prev, toSurface: fitsEnvChannel(picked, env, state) ? env?.surface : surfaceOfAgent(picked, state), agentName: picked.name });
  /* A different channel is a core change: the fork is the one question, and
     it already carries only the scenarios whose capabilities still exist —
     listing every tool added and removed beside it would ask the same thing
     a dozen times. */
  if (core) {
    const added = nudges.filter((x) => x.kind === "add").length;
    const removed = nudges.filter((x) => x.kind === "remove").length;
    const toolLine = added || removed ? ` Its tools differ too (${added} new, ${removed} missing); scenarios that need a missing one stay here.` : "";
    return { version, prev, nudges: [{ ...core, detail: `${core.detail}${toolLine}` }], core };
  }
  return { version, prev, nudges, core: null };
};

/**
 * The store calls a switch makes, in order: change the agent (its version list
 * continues with the new one) and record the nudges. Returns the plan.
 */
export const applyAgentSwitch = ({ env, envState, plan, agent, versions, activeLabel, changeEnvAgent, patch }) => {
  if (!plan) {
    changeEnvAgent(env.id, { agent, versions, activeLabel });
    return null;
  }
  changeEnvAgent(env.id, { agent, versions: [...agentVersions(envState), plan.version], activeLabel: plan.version.label });
  const merged = withNudges(envState, plan.nudges);
  if (merged !== envState) patch({ nudges: merged.nudges });
  return plan;
};

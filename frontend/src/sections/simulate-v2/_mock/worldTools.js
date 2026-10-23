/*
  Adding tools to an environment's world from an MCP server — and taking
  them out again.

  Tools join the world the way any world edit does. While no run has pinned
  the current version (a draft, or a version saved but not yet run) they land
  on it in place. Once a run has pinned it, the change waits as an unsaved
  edit beside the others — "N edits since v3 · Save as v4" — and is saved
  with them, never on its own behind them. Adding a tool doesn't touch a
  scenario's reference solution, so no proof goes stale; removing one that a
  scenario calls does. Scenarios for new tools are written on purpose.

  Each new tool starts Simulated — answered by the world, against its seeded
  data — and can be moved per tool on the World tab; only a tool that reads
  may ever go Live, once someone allows it.

  The server travels on each tool (`via`), so the world's tool sources are
  read off its tools: roll a version back and its sources go with it.
*/
import {
  agentVersions, currentEnvVersion, editsLandInPlace, environmentVersions, isEnvDraft,
  pendingEnvChanges, saveEnvChangesPatch,
} from "./versions";
import { agentToolsFor, worldToolsFor } from "./toolFit";
import { mcpNameOf, mcpServersOfAgent, worldToolFromMcp } from "./mcpServers";
import { replicationOf } from "./replication";

const slugOf = (s) => String(s || "server").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "server";
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const strip = ({ current, ...v }) => v; // eslint-disable-line no-unused-vars

/**
 * Where one of a server's tools lands in the world:
 *   { status: "new", as }       — joins under its own name
 *   { status: "renamed", as, clashWith } — its name is taken by another tool
 *     (another server's create_refund), so it joins as stripe_create_refund
 *   { status: "inWorld", as }   — the world answers it already
 * A tool the world read from the agent with the same name as one on the
 * agent's own server is the same tool, not a clash.
 */
export const mcpPlacementOf = (tool, server, worldTools, { agentServerIds } = {}) => {
  const same = worldTools.find((w) => w.via?.serverId === server.id && mcpNameOf(w) === tool.name);
  if (same) return { status: "inWorld", as: same.name };
  const clash = worldTools.find((w) => w.name === tool.name);
  if (!clash) return { status: "new", as: tool.name };
  if (!clash.via && agentServerIds?.has(server.id)) return { status: "inWorld", as: clash.name };
  const as = `${slugOf(server.label)}_${tool.name}`;
  if (worldTools.some((w) => w.name === as)) return { status: "inWorld", as };
  return { status: "renamed", as, clashWith: clash.via?.label || "this world" };
};

export const agentServerIdsOf = (envState) => new Set(mcpServersOfAgent(envState?.agent).map((s) => s.server.id));

/**
 * The MCP servers this world's tools came from, read off the tools:
 *   [{ id, label, host, transport, value, gateway, tokenRef, tools: [world names] }]
 */
export const envMcpServersOf = (env, envState) => {
  const byId = new Map();
  worldToolsFor(env, envState).forEach((t) => {
    if (t.via?.kind !== "mcp") return;
    const cur = byId.get(t.via.serverId) || { id: t.via.serverId, ...t.via, tools: [] };
    cur.tools.push(t.name);
    byId.set(t.via.serverId, cur);
  });
  return [...byId.values()];
};

/* The stored version list with the current one changed — for an edit that lands in place. */
const inPlaceVersions = (env, envState, change) => {
  const cur = currentEnvVersion(env, envState)?.label;
  return [...environmentVersions(env, envState)].reverse().map((v) => (v.label === cur ? change(strip(v)) : strip(v)));
};

/**
 * What adding `picked` tools from `server` does:
 *   { added, renamed, inPlace, versionLabel, waiting, envPatch, statePatch }
 * `waiting` is how many unsaved edits it joins.
 */
export const addMcpToolsPlan = (env, envState, { server, picked, now = new Date().toISOString() }) => {
  const current = worldToolsFor(env, envState);
  const agentServerIds = agentServerIdsOf(envState);
  const added = [];
  const renamed = [];
  picked.forEach((t) => {
    const place = mcpPlacementOf(t, server, [...current, ...added], { agentServerIds });
    if (place.status === "inWorld") return;
    if (place.status === "renamed") renamed.push({ from: t.name, as: place.as, clashWith: place.clashWith });
    added.push(worldToolFromMcp(t, server, { as: place.as }));
  });
  /* Simulated to start: there's nothing recorded for a tool the world just met. */
  const toolEnv = { ...(envState?.toolEnv || {}) };
  added.forEach((t) => { toolEnv[t.name] = { ...(toolEnv[t.name] || {}), tier: "stateful" }; });
  const note = `Added ${plural(added.length, "tool")} from ${server.label} ${server.gateway ? "through your Gateway" : "over MCP"}`;

  if (editsLandInPlace(envState)) {
    const next = [...current, ...added];
    const cur = currentEnvVersion(env, envState)?.label || "v1";
    return {
      added, renamed, inPlace: true, versionLabel: cur, waiting: 0,
      /* A draft's first build is the environment's own tool list too. */
      envPatch: isEnvDraft(envState) ? { tools: next, firstBuildTools: next } : null,
      statePatch: {
        toolEnv,
        envVersions: inPlaceVersions(env, envState, (v) => ({
          ...v, tools: next, note: [v.note, `${note}.`].filter(Boolean).join(" "),
          changed: [...new Set([...(v.changed || []), "tools-added"])],
        })),
      },
    };
  }
  const waiting = pendingEnvChanges(envState).length;
  return {
    added, renamed, inPlace: false, versionLabel: null, waiting,
    envPatch: null,
    statePatch: {
      toolEnv,
      unsavedEnvChanges: [...pendingEnvChanges(envState), { kind: "tools-added", detail: note, addTools: added, at: now }],
    },
  };
};

/**
 * Taking tools out of the world: in place while the version is unpinned,
 * otherwise an unsaved edit. A tool added in an edit not yet saved simply
 * leaves that edit.
 *   { removed, inPlace, envPatch, statePatch }
 */
export const removeWorldToolsPlan = (env, envState, { names, label, now = new Date().toISOString() }) => {
  const gone = new Set(names);
  const current = worldToolsFor(env, envState);
  const removed = current.filter((t) => gone.has(t.name)).map((t) => t.name);
  if (editsLandInPlace(envState)) {
    const next = current.filter((t) => !gone.has(t.name));
    return {
      removed, inPlace: true,
      envPatch: isEnvDraft(envState) ? { tools: next, firstBuildTools: next } : null,
      statePatch: {
        envVersions: inPlaceVersions(env, envState, (v) => ({ ...v, tools: next })),
      },
    };
  }
  /* Added and removed before either was saved: the add is undone, nothing recorded. */
  const pending = pendingEnvChanges(envState);
  const unsavedAdds = new Set(pending.flatMap((c) => (c.addTools || []).map((t) => t.name)));
  const trimmed = pending
    .map((c) => (c.addTools ? { ...c, addTools: c.addTools.filter((t) => !gone.has(t.name)) } : c))
    .filter((c) => !c.addTools || c.addTools.length);
  const saved = removed.filter((n) => !unsavedAdds.has(n));
  return {
    removed, inPlace: false,
    envPatch: null,
    statePatch: {
      unsavedEnvChanges: saved.length
        ? [...trimmed, { kind: "tools-removed", detail: `Removed ${saved.join(", ")}${label ? ` (${label})` : ""}`, removeTools: saved, at: now }]
        : trimmed,
    },
  };
};

/**
 * Add the tools and save the version in one go — a run about to start on a
 * pinned version only gets them if they're saved. Unsaved edits already
 * waiting are saved with them; the caller says so.
 */
export const addAndSaveMcpToolsPatch = (env, envState, { server, picked }) => {
  const plan = addMcpToolsPlan(env, envState, { server, picked });
  if (plan.inPlace) return { plan, envPatch: plan.envPatch, statePatch: plan.statePatch };
  const after = { ...envState, ...plan.statePatch };
  return { plan, envPatch: null, statePatch: { ...plan.statePatch, ...saveEnvChangesPatch(env, after) } };
};

/**
 * Whether the agent this environment tests can call a tool the world added
 * — it may not have it. Runs hand an added tool to agents we run ourselves
 * (a sealed copy, a prompt we run, an agent built here, code on our SDK);
 * a live agent on its platform only has the tools set up there.
 *   → null (the agent calls it already) | { kind, label, tip }
 */
export const toolReachOf = (tool, { agent, calls }) => {
  if (calls?.has(tool.name) || calls?.has(mcpNameOf(tool))) return null;
  if (!agent) return { kind: "pending", label: "For the agent you connect", tip: "Runs offer this tool to the agent once one is connected." };
  const proof = replicationOf(agent).proofLabel;
  if (proof === "Live") {
    return {
      kind: "blocked",
      label: "Agent can't see it",
      tip: `${agent.name || "The agent"} runs live, so it only has the tools set up where it runs. Add this MCP server to the agent there, or test a sealed copy from the agent's Connections.`,
    };
  }
  return { kind: "offered", label: "Offered to the agent", tip: `Runs give ${agent.name || "the agent"} this tool here. Its own setup doesn't change.` };
};

/**
 * The tools the agent itself calls, at the version this environment tests:
 * that version's tools when any version declares them, else what was read
 * from it when it was connected — plus whatever its own MCP servers list.
 * An added tool outside this set is one the agent doesn't have.
 */
export const agentCallsOf = (env, envState) => {
  const agent = envState?.agent;
  if (!agent) return new Set();
  const declared = agentVersions(envState).some((v) => v.tools?.length);
  const own = declared || !envState?.toolsRead?.names
    ? agentToolsFor(env, envState).map((t) => t.name)
    : envState.toolsRead.names;
  return new Set([...own, ...mcpServersOfAgent(agent).flatMap((s) => s.tools.map((t) => t.name))]);
};

/**
 * The agent's own MCP tools a run would get no answer for — what its servers
 * list that the version a run uses doesn't answer:
 *   { groups: [{ server, tools }], missing: [tools], unsaved: [names] }
 * `unsaved` are ones added on the World tab but not saved into a version yet.
 */
export const mcpRunGapOf = (env, envState) => {
  const agent = envState?.agent;
  if (!agent) return { groups: [], missing: [], unsaved: [] };
  const savedLabel = currentEnvVersion(env, envState)?.label;
  const names = (list) => new Set(list.flatMap((t) => [t.name, mcpNameOf(t)]));
  const saved = names(worldToolsFor(env, envState, savedLabel));
  const edited = names(worldToolsFor(env, envState));
  const groups = mcpServersOfAgent(agent)
    .map(({ server, tools }) => ({ server, tools: tools.filter((t) => !saved.has(t.name)) }))
    .filter((g) => g.tools.length);
  const missing = groups.flatMap((g) => g.tools);
  return { groups, missing, unsaved: missing.filter((t) => edited.has(t.name)).map((t) => t.name) };
};

/**
 * What an environment built from an MCP server sets up for its tools — the
 * same as tools added later from the World tab: each Simulated to start,
 * since nothing is recorded for a world that just met them.
 *   (tools) → { toolEnv } | null
 */
export const mcpBuildStateOf = (tools) => {
  const fromMcp = (tools || []).filter((t) => t.via?.kind === "mcp");
  if (!fromMcp.length) return null;
  return { toolEnv: Object.fromEntries(fromMcp.map((t) => [t.name, { tier: "stateful" }])) };
};

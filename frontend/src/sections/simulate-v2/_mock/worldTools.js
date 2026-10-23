/*
  Adding tools to an environment's world from an MCP server.

  Tools join the world the way any world edit does: a never-run environment
  (a draft) takes them in place, one that has run gets the next environment
  version. Adding a tool doesn't touch a scenario's reference solution, so
  no proof goes stale; scenarios for the new tools are written on purpose.
  Each new tool starts Simulated — answered by the world, against its seeded
  data — and can be moved per tool on the World tab; only a tool that reads
  may ever go Live, once someone allows it.
*/
import { environmentVersions, isEnvDraft, nextEnvVersion } from "./versions";
import { agentToolsFor, worldToolsFor } from "./toolFit";
import { agentMcpTools, worldToolFromMcp } from "./mcpServers";
import { replicationOf } from "./replication";

/** The MCP servers this environment has added tools from: [{ id, host, label, transport, value, tokenRef, tools, addedAt }]. */
export const envMcpServersOf = (envState) => envState?.mcpServers || [];

/**
 * What adding `picked` tools from `server` does:
 *   { envPatch, statePatch, added, versionLabel, inPlace }
 */
export const addMcpToolsPlan = (env, envState, { server, picked, now = new Date().toISOString() }) => {
  const current = worldToolsFor(env, envState);
  const have = new Set(current.map((t) => t.name));
  const added = picked.filter((t) => !have.has(t.name)).map((t) => worldToolFromMcp(t, server));
  const next = [...current, ...added];
  const known = envMcpServersOf(envState).filter((s) => s.id !== server.id);
  const prior = envMcpServersOf(envState).find((s) => s.id === server.id);
  const mcpServers = [...known, { ...server, tools: [...new Set([...(prior?.tools || []), ...added.map((t) => t.name)])], addedAt: prior?.addedAt || now }];
  /* Simulated to start: there's nothing recorded for a tool the world just met. */
  const toolEnv = { ...(envState?.toolEnv || {}) };
  added.forEach((t) => { toolEnv[t.name] = { ...(toolEnv[t.name] || {}), tier: "stateful" }; });
  const note = `Added ${added.length} tool${added.length === 1 ? "" : "s"} from ${server.label} (MCP).`;

  if (isEnvDraft(envState)) {
    const stored = envState?.envVersions || [];
    const envVersions = stored.length
      ? stored.map((v, i) => (i === stored.length - 1 ? { ...v, tools: next, note: [v.note, note].filter(Boolean).join(" ") } : v))
      : stored;
    return {
      added, inPlace: true, versionLabel: environmentVersions(env, envState)[0]?.label || "v1",
      envPatch: { tools: next, firstBuildTools: next },
      statePatch: { mcpServers, toolEnv, ...(stored.length && { envVersions }) },
    };
  }
  const minted = nextEnvVersion(env, envState, { changed: ["tools-added"], note, now, tools: next });
  const list = envState?.envVersions?.length ? envState.envVersions : [...environmentVersions(env, envState)].reverse();
  return {
    added, inPlace: false, versionLabel: minted.label,
    envPatch: null,
    statePatch: { mcpServers, toolEnv, envVersions: [...list, minted], activeEnvVersion: minted.label, unsavedEnvChanges: [] },
  };
};

/**
 * What an environment built from an MCP server records about it — the same
 * as tools added later from the World tab: the server, and each of its tools
 * Simulated to start, since nothing is recorded for a world that just met them.
 *   (tools, worldSource, now) → { mcpServers, toolEnv } | null
 */
export const mcpBuildStateOf = (tools, worldSource, now = new Date().toISOString()) => {
  const fromMcp = (tools || []).filter((t) => t.via?.kind === "mcp");
  if (!fromMcp.length) return null;
  const { via } = fromMcp[0];
  const server = {
    id: via.serverId, host: via.host, label: via.label,
    transport: worldSource?.transport || "http", value: worldSource?.value || via.host,
    ...(worldSource?.tokenRef && { tokenRef: worldSource.tokenRef }),
  };
  const toolEnv = Object.fromEntries(fromMcp.map((t) => [t.name, { tier: "stateful" }]));
  return { mcpServers: [{ ...server, tools: fromMcp.map((t) => t.name), addedAt: now }], toolEnv };
};

/**
 * Whether the agent this environment tests can call a tool the world added
 * — it may not have it. Runs hand an added tool to agents we run ourselves
 * (a sealed copy, a prompt we run, an agent built here, code on our SDK);
 * a live agent on its platform only has the tools set up there.
 *   → null (the agent calls it already) | { kind, label, tip }
 */
export const toolReachOf = (tool, { agent, calls }) => {
  if (calls?.has(tool.name)) return null;
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
 * The tools the agent itself calls: what was read from it when it was
 * connected, else its version's tools — plus whatever its own MCP servers
 * list. An added tool outside this set is one the agent doesn't have.
 */
export const agentCallsOf = (env, envState) => {
  const agent = envState?.agent;
  if (!agent) return new Set();
  const own = envState?.toolsRead?.names || agentToolsFor(env, envState).map((t) => t.name);
  return new Set([...own, ...agentMcpTools(agent).map((t) => t.name)]);
};

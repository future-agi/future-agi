import { MODALITY } from "../../agentTypes";
import { MOCK_DERIVATION, STEP_FALLBACKS } from "./agents.constants";

// Resolve an agent's typeId to a modality descriptor ({ label, ... }). The
// environments world keys agents by AGENT_TYPES ids (voice/text); provider
// strings and "auto" won't match, so callers keep a label fallback.
const getAgentType = (id) => MODALITY[id];

// The name shown for an agent. Prefer an explicit id the platform gave it, then
// a repo name, then an endpoint / MCP name, then a file stem; last resort is a
// stable short id derived from the agent's id so unnamed agents stay distinct.
export function deriveAgentName(agent, type) {
  const values = agent.values || {};

  if (values.agentId) return values.agentId;
  if (values.assistantId) return values.assistantId;
  if (values.agentName) return values.agentName;

  if (values.repoUrl) return repoNameFrom(values.repoUrl);
  if (agent.location && agent.location.includes("github.com")) return repoNameFrom(agent.location);

  if (values.endpoint) return endpointNameFrom(values.endpoint);
  if (values.mcpUrl) return short(values.mcpUrl);

  if (values.file || values.filename) return (values.file || values.filename).replace(/\.[^.]+$/, "");

  const suffix = String(agent.id || "").slice(-6) || "unnamed";
  const stem = (type?.label || "agent").split(" ")[0].toLowerCase();
  return `${stem}-${suffix}`;
}

// Secondary line under the name: what kind of agent this is + where it lives,
// in that order. Keeps things scannable without repeating the name.
export function deriveTypeLine(agent, type) {
  const values = agent.values || {};
  const parts = [type?.label];
  if (values.provider) parts.push(values.provider);
  else if (values.repoUrl) parts.push(`branch: ${values.ref?.value || "main"}`);
  else if (values.endpoint) parts.push(short(values.endpoint));
  else if (values.mcpUrl) parts.push("MCP");
  if (values.direction || values.callDirection) parts.push(values.direction || values.callDirection);
  return parts.filter(Boolean).join(" · ");
}

// Last path segment of a repo URL, without a trailing slash or .git.
export function repoNameFrom(url) {
  if (!url) return "repo";
  const clean = url.replace(/\.git$/, "").replace(/\/$/, "");
  const seg = clean.split("/").filter(Boolean).pop() || "repo";
  return seg;
}

// Last path segment of an endpoint URL, falling back to the host, then the
// stripped string when the URL won't parse.
export function endpointNameFrom(url) {
  if (!url) return "endpoint";
  try {
    const u = new URL(url);
    const seg = u.pathname.split("/").filter(Boolean).pop();
    return seg || u.hostname;
  } catch {
    return short(url);
  }
}

// Drop the protocol and leading www. for display.
export function short(u) {
  if (!u) return "";
  return u.replace(/^https?:\/\//, "").replace(/^www\./, "");
}

// The connection-detail rows for an agent, keyed off which connection fields it
// carries (voice platform / repo / endpoint / MCP), else a bare Kind row.
export function connectionRowsFor(agent, type, values) {
  if (values.provider) {
    return [
      { label: "Channel", value: type?.channel || "Voice" },
      { label: "Provider", value: values.provider, mono: true },
      { label: "Agent name", value: values.agentId || values.assistantId || "—", mono: true },
      { label: "Call direction", value: values.direction || "inbound", mono: true },
    ];
  }
  if (values.repoUrl) {
    return [
      { label: "Kind", value: "Repository" },
      { label: "URL", value: values.repoUrl, mono: true },
      values.ref && { label: "Pinned to", value: `${values.ref.kind} · ${values.ref.value}`, mono: true },
    ].filter(Boolean);
  }
  if (values.endpoint) {
    return [
      { label: "Kind", value: "Running endpoint" },
      { label: "URL", value: values.endpoint, mono: true },
    ];
  }
  if (values.mcpUrl) {
    return [
      { label: "Kind", value: "MCP server" },
      { label: "URL", value: values.mcpUrl, mono: true },
    ];
  }
  return [{ label: "Kind", value: type?.label || "Agent" }];
}

// The "where it came from" rows: location, how it was attached, and any note. An
// agent with no location gets a one-line explanation of how it was read.
export function sourceRowsFor(agent) {
  const values = agent.values || {};
  const attached = agent.connectedAt ? new Date(agent.connectedAt).toLocaleString() : null;
  const rows = [];
  if (values.repoUrl) rows.push({ label: "Location", value: values.repoUrl, mono: true });
  if (values.endpoint) rows.push({ label: "Location", value: values.endpoint, mono: true });
  if (values.mcpUrl) rows.push({ label: "Location", value: values.mcpUrl, mono: true });
  if (agent.via) rows.push({ label: "Attached via", value: agent.via });
  if (attached) rows.push({ label: "Attached at", value: attached });
  if (agent.note) rows.push({ label: "Note", value: agent.note });
  if (rows.length === 0) rows.push({ label: "How we read it", value: "Read from the imports and call sites in the source." });
  return rows;
}

// Wrap a bare agent (no versions[]) as its own initial version so every agent —
// source or additional, freshly attached or stored before versioning existed —
// has the same shape after normalisation. Idempotent.
export function normalizeAgentVersions(agent) {
  if (!agent) return agent;
  if (Array.isArray(agent.versions) && agent.versions.length > 0) return agent;
  const v1 = {
    id: "v1",
    label: "v1",
    values: agent.values || {},
    via: agent.via,
    connectedAt: agent.connectedAt || new Date().toISOString(),
    note: agent.note || "Initial version",
  };
  return { ...agent, versions: [v1], activeVersionId: "v1" };
}

// Map a source-agent version record onto the env-level agentVersions shape the
// header pill + Overview summary read.
export function toEnvAgentVersion(v) {
  return {
    id: v.id,
    label: v.label,
    note: v.note,
    reach: v.via || v.reach || "endpoint",
    createdAt: v.connectedAt || v.createdAt || new Date().toISOString(),
  };
}

// Mint the next version record, keyed sequentially off the existing stack
// (v1 -> v2 -> v3). Not tied to time so the label reads the same regardless of
// when it was minted.
export function mintNextVersion(agent, record) {
  const existing = agent.versions || [];
  const nextNumber = existing.length + 1;
  const versionId = `v${nextNumber}`;
  return {
    id: versionId,
    label: `v${nextNumber}`,
    values: record?.values || {},
    via: record?.via,
    connectedAt: record?.connectedAt || new Date().toISOString(),
    note: record?.note || `Version ${nextNumber}`,
  };
}

// Copy the active version's connection fields up to the agent's top level so any
// downstream reader of agent.values / via / connectedAt / note sees the chosen
// version's data without knowing versions exist. The stack lives underneath.
export function applyActiveVersion(agent) {
  const active = (agent.versions || []).find((v) => v.id === agent.activeVersionId)
    || (agent.versions || [])[0];
  if (!active) return agent;
  return {
    ...agent,
    values: active.values,
    via: active.via,
    connectedAt: active.connectedAt,
    note: active.note,
  };
}

// Builder-turn steps for switching between existing versions (forward via Set
// active, or backward via Roll back). Same shape as a version upgrade — the env
// re-derives against whichever version is now active — with narration that
// reflects the version already existed.
export function buildVersionSwitchSteps({ agent, from, to, isRollback }) {
  const typeLabel = getAgentType(agent?.typeId)?.label || STEP_FALLBACKS.agent;
  const fromLabel = from?.label || STEP_FALLBACKS.previousVersion;
  const via = to?.via || to?.values?.endpoint || agent?.via || STEP_FALLBACKS.versionEndpoint;
  const verb = isRollback ? "Rolling back to" : "Switching to";
  const m = MOCK_DERIVATION.switch;
  return [
    { kind: "think", text: `${verb} ${typeLabel} · ${to.label} (previously ${fromLabel}). Re-reading it and refreshing what depends on it.` },
    { kind: "tool", label: `read_agent(${via})`, result: `${to.label} · loaded` },
    { kind: "tool", label: "extract_tools()", result: m.toolsResult },
    { kind: "tool", label: "extract_rules()", result: m.rulesResult },
    { kind: "note", text: `Contract regenerated against ${to.label}.` },
    { kind: "tool", label: "re_derive_scenarios()", result: m.scenariosResult },
    { kind: "tool", label: "reevaluate_preset_evals()", result: m.evalsResult },
    { kind: "note", text: `Environment now testing ${typeLabel} · ${to.label}. Runs from this point on are stamped with this version.` },
  ];
}

// Builder-turn steps for a new version landing and re-deriving the env. A short,
// believable chain in the same shape as the original agent-source derivation, so
// the chat panel narrates what the environment now tests against.
export function buildVersionUpgradeSteps({ agent, next }) {
  const typeLabel = getAgentType(agent?.typeId)?.label || STEP_FALLBACKS.agent;
  const via = next?.via || agent?.via || next?.values?.endpoint || STEP_FALLBACKS.attachedAgent;
  const m = MOCK_DERIVATION.upgrade;
  return [
    { kind: "think", text: `${typeLabel} · ${next.label} is now the version this environment tests against. Re-reading the agent and refreshing what depends on it.` },
    { kind: "tool", label: `read_agent(${via})`, result: `${next.label} · loaded` },
    { kind: "tool", label: "extract_tools()", result: m.toolsResult },
    { kind: "tool", label: "extract_rules()", result: m.rulesResult },
    { kind: "note", text: "Contract regenerated. Comparing against the previous version." },
    { kind: "json", label: `contract diff · ${next.label} vs previous`, value: JSON.stringify(m.diff, null, 2) },
    { kind: "tool", label: "re_derive_scenarios()", result: m.scenariosResult },
    { kind: "tool", label: "reevaluate_preset_evals()", result: m.evalsResult },
    { kind: "note", text: `Environment is now testing ${typeLabel} · ${next.label}. Runs from this point on are stamped with the new version.` },
  ];
}

// Builder-turn steps for promoting an additional agent to source: re-read from
// it, regenerate the contract, and leave the old source attached as additional.
export function buildPromoteSteps({ from, to }) {
  const fromLabel = getAgentType(from?.typeId)?.label || STEP_FALLBACKS.previousSource;
  const toLabel = getAgentType(to?.typeId)?.label || STEP_FALLBACKS.newSource;
  const toVia = to?.via || to?.location || STEP_FALLBACKS.attachedSource;
  const m = MOCK_DERIVATION.promote;
  return [
    { kind: "think", text: `Setting ${toLabel} as the source of this environment. Detaching ${fromLabel}.` },
    { kind: "tool", label: `read_agent(${toVia})`, result: "loaded" },
    { kind: "tool", label: "extract_tools()", result: m.toolsResult },
    { kind: "tool", label: "extract_rules()", result: m.rulesResult },
    { kind: "note", text: "Contract regenerated. Comparing against the previous derivation." },
    { kind: "json", label: "contract diff", value: JSON.stringify(m.diff, null, 2) },
    { kind: "tool", label: "re_derive_scenarios()", result: m.scenariosResult },
    { kind: "tool", label: "reevaluate_preset_evals()", result: m.evalsResult },
    { kind: "note", text: `${fromLabel} is still attached as an additional agent. Runs default to the new source; switch back any time from the Agents tab.` },
  ];
}

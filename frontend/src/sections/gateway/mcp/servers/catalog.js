// The Servers catalog: each org MCP server joined with its live health (the
// status API) and its credential (config reference + vault metadata), and
// the words the connect flow uses.

import {
  getMCPServerId,
  getMCPServerToolCount,
  serverHealth,
  urlHost,
} from "../utils";

const KIND_LABEL = { bearer: "Bearer token", api_key: "API key" };

// How a server authenticates upstream. A vault reference
// (credential {mode: shared, credential_id, state}) takes its kind and header
// from the vault list when it can; the legacy inline auth form is read as
// set or not set. Never a value.
export function serverAuth(serverConfig, vaultRows = []) {
  const credential = serverConfig?.credential;
  if (
    credential &&
    typeof credential === "object" &&
    credential.mode &&
    credential.mode !== "none"
  ) {
    const row = vaultRows.find((r) => r.id === credential.credential_id);
    const state = credential.state || "active";
    const kind = row?.kind && KIND_LABEL[row.kind] ? row.kind : "stored";
    return {
      kind,
      label: KIND_LABEL[kind] || "Stored credential",
      state: state === "active" ? "set" : state,
      header: row?.header_name || "",
      credentialId: credential.credential_id,
    };
  }
  const auth = serverConfig?.auth;
  if (auth?.type && auth.type !== "none") {
    return {
      kind: auth.type,
      label: KIND_LABEL[auth.type] || auth.type,
      state: auth.token || auth.key ? "set" : "not_set",
      header: auth.header || "",
    };
  }
  return { kind: "none", label: "No auth", state: "none", header: "" };
}

export function serverRows(mcp, status, vaultRows = []) {
  const servers =
    mcp?.servers && typeof mcp.servers === "object" ? mcp.servers : {};
  const health = {};
  (status?.servers || []).forEach((row) => {
    const id = getMCPServerId(row);
    if (id) health[id] = row;
  });
  return Object.keys(servers)
    .sort()
    .map((id) => {
      const config = servers[id] || {};
      const live = health[id];
      return {
        id,
        config,
        url: config.url || "",
        host: urlHost(config.url),
        transport: (config.transport || "http").toUpperCase(),
        health: serverHealth(live, config),
        live,
        toolCount: live ? getMCPServerToolCount(live) : 0,
        lastError: live?.last_error || "",
        era: live?.era || config.era || "",
        lastRefreshed: live?.last_refreshed_at || "",
        auth: serverAuth(config, vaultRows),
      };
    });
}

export const STATUS_FILTERS = [
  { value: "connected", label: "Connected" },
  { value: "unhealthy", label: "Unhealthy" },
  { value: "disabled", label: "Disabled" },
  { value: "not_connected", label: "Not connected" },
];

export const AUTH_FILTERS = [
  { value: "none", label: "No auth" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key" },
  { value: "stored", label: "Stored credential" },
];

function statusKey(row) {
  if (row.health.label === "Disabled") return "disabled";
  if (!row.live) return "not_connected";
  if (row.health.tone === "healthy") return "connected";
  if (row.health.tone === "error") return "unhealthy";
  return "not_connected";
}

export function filterServers(
  rows,
  { search = "", status = "", auth = "" } = {},
) {
  const term = search.trim().toLowerCase();
  return rows.filter(
    (row) =>
      (!status || statusKey(row) === status) &&
      (!auth || row.auth.kind === auth) &&
      (!term || `${row.id} ${row.url}`.toLowerCase().includes(term)),
  );
}

// Stored non-secret fields an edit keeps: the dialog does not edit them, and
// update-mcp-server replaces the whole entry. tools_cache_ttl is left out on
// purpose: the gateway does not read it on this line.
const CARRIED = [
  "enabled",
  "timeout_ms",
  "era",
  "forward_headers",
  "labels",
  "propagate_depth",
  "trusted_annotations",
];

export function carriedFields(serverConfig) {
  const out = {};
  CARRIED.forEach((key) => {
    if (serverConfig?.[key] !== undefined) out[key] = serverConfig[key];
  });
  return out;
}

const PROBE_ERRORS = {
  not_mcp:
    "This URL did not answer as an MCP server. Use the server's MCP endpoint (it often ends in /mcp).",
};

// mcp-probe-v1's result in a sentence.
export function probeMessage(result) {
  if (result?.ok) {
    const info = result.server_info || {};
    const name = [info.title || info.name, info.version]
      .filter(Boolean)
      .join(" ");
    const tools = `${result.tool_count ?? 0} ${result.tool_count === 1 ? "tool" : "tools"}`;
    const parts = [tools];
    if (result.era) parts.push(`protocol ${result.era}`);
    if (result.latency_ms !== undefined) parts.push(`${result.latency_ms}ms`);
    return {
      ok: true,
      text: `Connected${name ? ` to ${name}` : ""}: ${parts.join(", ")}.`,
    };
  }
  const code = result?.error || "unknown";
  return {
    ok: false,
    text: PROBE_ERRORS[code] || `The server did not connect: ${code}.`,
  };
}

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

// With statusRead false (the status read failed), each server's health is
// unknown, whatever an earlier read said.
export function serverRows(
  mcp,
  status,
  vaultRows = [],
  { statusRead = true } = {},
) {
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
        health: serverHealth(live, config, { statusRead }),
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

// A card files under the status API's verdict on its row. While the status
// read fails the chip says the status is unknown, but a failed refresh does
// not move a card to another filter.
function statusKey(row) {
  const verdict = serverHealth(row.live, row.config);
  if (verdict.label === "Disabled") return "disabled";
  if (!row.live) return "not_connected";
  if (verdict.tone === "healthy") return "connected";
  if (verdict.tone === "error") return "unhealthy";
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

// The tools cache TTL under each name a stored server may carry it: the
// seconds, and the older duration string ("5m") the gateway reads when there
// are no seconds.
export const TTL_KEYS = [
  "tools_cache_ttl_seconds",
  "toolsCacheTtlSeconds",
  "toolsCacheTTLSeconds",
  "tools_cache_ttl",
  "toolsCacheTtl",
  "toolsCacheTTL",
];

// Stored non-secret fields an edit keeps: the dialog does not edit them, and
// update-mcp-server replaces the whole entry. Keep the allowlist explicit so
// credentials, operator-only fields and unknown settings are never echoed.
// A field stored under its other spelling (update-mcp-server's contract takes
// both) is sent back under that spelling, unchanged.
const CARRIED = [
  "enabled",
  "timeout_ms",
  "timeoutMs",
  "era",
  "forward_headers",
  "forwardHeaders",
  "labels",
  "propagate_depth",
  "propagateDepth",
  "trusted_annotations",
  "trustedAnnotations",
  "catalog_ref",
  // The same record under the other spelling the gateway reads: an edit sends
  // it back unchanged, or the save is refused.
  "catalogRef",
  "url_variables",
  "urlVariables",
  "toolkits",
  "oauth",
  ...TTL_KEYS,
];

export function carriedFields(serverConfig) {
  const out = {};
  CARRIED.forEach((key) => {
    if (serverConfig?.[key] !== undefined) out[key] = serverConfig[key];
  });
  return out;
}

// A Go duration string in seconds, as time.ParseDuration reads it ("5m",
// "1h30m", "1.5h", "0"), or null for one it refuses.
const DURATION_PART = /(\d+\.?\d*|\.\d+)(ns|us|µs|μs|ms|s|m|h)/g;
const DURATION = /^[-+]?(?:(?:\d+\.?\d*|\.\d+)(?:ns|us|µs|μs|ms|s|m|h))+$/;
const SECONDS_PER = {
  ns: 1e-9,
  us: 1e-6,
  µs: 1e-6,
  μs: 1e-6,
  ms: 1e-3,
  s: 1,
  m: 60,
  h: 3600,
};
function durationSeconds(text) {
  if (typeof text !== "string") return null;
  if (/^[-+]?0$/.test(text)) return 0;
  if (!DURATION.test(text)) return null;
  let total = 0;
  for (const [, amount, unit] of text.matchAll(DURATION_PART))
    total += Number(amount) * SECONDS_PER[unit];
  return text.startsWith("-") ? -total : total;
}

// A stored tools cache TTL in seconds, as the gateway reads it
// (agentcc-gateway tenant/config_mcp.go toolsCacheTTLSeconds): the seconds,
// else the older duration string, whole seconds, else none (its 300).
function storedTtlSeconds(cfg) {
  if (Number.isInteger(cfg.tools_cache_ttl_seconds))
    return cfg.tools_cache_ttl_seconds;
  const seconds = durationSeconds(cfg.tools_cache_ttl);
  return seconds != null && seconds >= 0 ? Math.floor(seconds) : null;
}

// Edit only non-secret fields. A missing enabled flag keeps the saved default.
export function serverForm(editServer, hasCredential = false) {
  const cfg = editServer?.config || {};
  const ttl = storedTtlSeconds(cfg);
  return {
    server_id: editServer?.serverId || editServer?.server_id || "",
    url: cfg.url || "",
    enabled: cfg.enabled,
    authType: hasCredential ? "keep" : "none",
    token: "",
    header: cfg.auth?.header || editServer?.storedHeader || "",
    apiKey: "",
    toolsCacheTtl: ttl == null ? "" : String(ttl),
  };
}

// The transient probe uses a typed secret or the stored vault reference. A
// secret left blank on edit keeps the stored one, so the probe uses it too.
export function probeAuth(form, editServer) {
  if (form.authType === "bearer" && form.token.trim()) {
    return { auth: { type: "bearer", value: form.token.trim() } };
  }
  if (form.authType === "api_key" && form.apiKey.trim()) {
    return {
      auth: {
        type: "api_key",
        header: form.header.trim() || "X-API-Key",
        value: form.apiKey.trim(),
      },
    };
  }
  const credentialId = editServer?.config?.credential?.credential_id;
  if (form.authType !== "none" && credentialId) {
    return { credential_id: credentialId };
  }
  return {};
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
      text: `Test passed${name ? ` for ${name}` : ""}: ${parts.join(", ")}. Save this server to keep the configuration.`,
    };
  }
  const code = result?.error || "unknown";
  return {
    ok: false,
    text: PROBE_ERRORS[code] || `The server did not connect: ${code}.`,
  };
}

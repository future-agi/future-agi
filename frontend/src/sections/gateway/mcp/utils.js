export function getMCPServerId(server) {
  return server?.server_id || server?.id || server?.name || "";
}

export function getMCPServerToolCount(server) {
  return Number(server?.tool_count ?? server?.toolCount ?? server?.tools ?? 0);
}

export function getMCPServerStatusLabel(server) {
  if (!server) return "Unknown";
  if (server.healthy === true) return "Healthy";
  if (server.healthy === false) return "Unhealthy";

  const status = String(server.status || "").trim();
  if (!status) return "Unknown";

  return status
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function getMCPServerStatusColor(server) {
  if (!server) return "default";
  if (server.healthy === true) return "success";
  if (server.healthy === false) return "error";

  const status = String(server.status || "").toLowerCase();
  if (["healthy", "connected", "running", "ok"].includes(status)) {
    return "success";
  }
  if (
    ["unhealthy", "error", "failed", "down", "disconnected"].includes(status)
  ) {
    return "error";
  }
  if (status === "configured") return "info";
  return "default";
}

// A server's stored upstream credential is write-only in the console: a page
// may say whether one is set, and must never show or send back its value.
// `auth` holds an inline token or key; `credential` (org config `mcp` v2)
// is a vault reference whose `secret` is push-only.
export function hasStoredCredential(serverConfig) {
  const auth = serverConfig?.auth || {};
  if (auth.token || auth.key) return true;

  const credential = serverConfig?.credential;
  if (!credential || typeof credential !== "object") return false;
  if (credential.secret) return true;
  if (!credential.mode || credential.mode === "none") return false;
  return (credential.state ?? "active") === "active";
}

// A copy of a stored server config with every secret value removed.
export function stripSecrets(serverConfig) {
  const safe = { ...serverConfig };
  if (safe.auth) {
    safe.auth = { ...safe.auth };
    delete safe.auth.token;
    delete safe.auth.key;
  }
  if (safe.credential) {
    safe.credential = { ...safe.credential };
    delete safe.credential.secret;
  }
  return safe;
}

// MCP counts as enabled only when the status API says `enabled: true`. A
// missing, null or non-boolean flag is not enabled.
export function isMCPEnabled(status) {
  return status?.enabled === true;
}

// Django's status `reason` and error `code` when the deployment runs with MCP
// off: the gateway has no MCP routes and answers every MCP read with 404.
export const MCP_OFF_CODE = "gateway_mcp_disabled";

// MCP is turned off for the whole deployment (not an outage, not an org with
// no servers): the status says so with its reason.
export function isMCPTurnedOff(status) {
  return status?.enabled !== true && status?.reason === MCP_OFF_CODE;
}

// A read Django refused because the deployment runs with MCP off.
export function isMCPOffError(error) {
  return error?.code === MCP_OFF_CODE;
}

// The line an MCP list shows when it has no row: that the read failed, that
// there is nothing to list, or that the filters match nothing.
export function emptyListText(isError, items, noun, noneText, noMatchText) {
  if (isError) return `MCP ${noun} could not be loaded from the gateway.`;
  return items.length === 0 ? noneText : noMatchText;
}

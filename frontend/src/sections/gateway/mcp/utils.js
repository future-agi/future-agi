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

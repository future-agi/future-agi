import { HOST_API } from "src/config-global";

// The MCP endpoint of the deployment this app talks to: its API base from
// config (config-global's HOST_API: runtime VITE_HOST_API, then build time,
// then the hosted host map) plus /mcp. Anything a user copies names this
// deployment, never a hard-coded production host. Null only when
// no API base is configured at all.
export function deploymentMcpUrl(apiBase = HOST_API) {
  const base = String(apiBase || "")
    .trim()
    .replace(/\/+$/, "");
  return base ? `${base}/mcp` : null;
}

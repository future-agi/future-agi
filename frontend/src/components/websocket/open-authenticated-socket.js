import { HOST_API } from "src/config-global";

// The server selects the fixed protocol on both acceptance and auth rejection.
export function openAuthenticatedSocket(path, token, workspaceId) {
  const scheme = HOST_API.startsWith("https://") ? "wss" : "ws";
  const host = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const url = new URL(`${scheme}://${host}${path}`);
  if (workspaceId) url.searchParams.set("workspace_id", workspaceId);

  // Subprotocol offers must be HTTP tokens, including for padded credentials.
  const encoded = encodeURIComponent(token)
    .replace(/\(/g, "%28")
    .replace(/\)/g, "%29");
  return new WebSocket(url.href, ["fi.auth.v1", `bearer.${encoded}`]);
}

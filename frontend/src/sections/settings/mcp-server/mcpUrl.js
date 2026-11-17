// L7c g1 (the capability contract b): the MCP address shown in Settings > MCP Server is
// the one of the environment the user is on: the server's own `mcp_url`, or
// the app's configured API base + `/mcp`. Never a fixed production host;
// null (the guide is not shown) when neither is known. Pure.
export function mcpUrlOf(configUrl, hostApi) {
  const own = String(configUrl ?? "").trim();
  if (own) return own;
  const host = String(hostApi ?? "")
    .trim()
    .replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(host)) return null;
  return `${host}/mcp`;
}

// Snippet generators for the MCP connect panel. Pure string builders — nothing
// here reaches a network; the published address is illustrative until the real
// MCP bridge lands.

function slug(name) {
  return String(name || "environment")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "environment";
}

// The published MCP address for a target — the version's connection when the
// user's own client connects to us.
export function mcpEndpointFor(target) {
  return `https://mcp.futureagi.com/e/${slug(target?.name)}`;
}

// The MCP client config a user pastes into Claude Desktop / Cursor / VS Code.
export function mcpConfig(target) {
  return JSON.stringify(
    {
      mcpServers: {
        [slug(target?.name)]: {
          url: mcpEndpointFor(target),
          transport: "http",
        },
      },
    },
    null,
    2,
  );
}

// A minimal Python session that connects to the same address.
export function mcpPythonSnippet(target) {
  return [
    "from mcp.client import connect",
    "",
    `session = connect("${mcpEndpointFor(target)}")`,
    "tools = session.list_tools()",
    "# hand `tools` to your agent and act in the environment",
  ].join("\n");
}

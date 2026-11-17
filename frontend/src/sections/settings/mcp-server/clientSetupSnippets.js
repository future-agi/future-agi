// The copyable configurations displayed by ClientSetupGuide. Keep these pure
// so the one-deployment API-base contract covers the actual shipped snippets.
export function buildMcpClientConfigs(mcpUrl) {
  return {
    cursor: JSON.stringify(
      { mcpServers: { futureagi: { url: mcpUrl } } },
      null,
      2,
    ),
    claudeCode: `claude mcp add futureagi --transport http ${mcpUrl}`,
    vscode: JSON.stringify(
      { "mcp.servers": { futureagi: { type: "http", url: mcpUrl } } },
      null,
      2,
    ),
    claudeDesktop: JSON.stringify(
      { mcpServers: { futureagi: { url: mcpUrl } } },
      null,
      2,
    ),
    windsurf: JSON.stringify(
      { mcpServers: { futureagi: { serverUrl: mcpUrl } } },
      null,
      2,
    ),
  };
}

import { mcpEndpoint, SLUG_PATTERN } from "../virtual/virtualServers";

// One table for the console and format-conformance tests. No API accepts a
// key value: clients resolve the environment placeholder on the user's device.
// Public format references are links, not a claim of installed-client proof.
const json = (value) => JSON.stringify(value, null, 2);
export function clientInstalls(baseUrl, slug) {
  const base = new URL(baseUrl);
  if (
    !["https:", "http:"].includes(base.protocol) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    !SLUG_PATTERN.test(slug)
  ) {
    throw new Error(
      "Choose a virtual server with a credential-free HTTP gateway URL.",
    );
  }
  const endpoint = mcpEndpoint(base.href, slug);
  const headers = { Authorization: "Bearer ${env:FUTUREAGI_KEY}" };
  const cursor = { url: endpoint, headers };
  const vscode = { type: "http", url: endpoint, headers };
  return [
    {
      id: "claude",
      name: "Claude web and Desktop",
      endpoint,
      format: "url",
      snippet: endpoint,
      note: "Add a custom connector in Settings > Connectors. Remote connectors use OAuth sign-in, not a pasted gateway key. Requires accepted client sign-in and a publicly reachable gateway.",
      docs: "https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp",
    },
    {
      id: "claude-code",
      name: "Claude Code",
      endpoint,
      format: "shell",
      snippet: `claude mcp add --transport http futureagi '${endpoint.replaceAll("'", "'\\''")}' --header "Authorization: Bearer $FUTUREAGI_KEY"`,
      note: "Export FUTUREAGI_KEY in your shell first. The CLI may persist the resolved header locally; protect your client configuration.",
      docs: "https://code.claude.com/docs/en/mcp",
    },
    {
      id: "cursor",
      name: "Cursor",
      endpoint,
      format: "json",
      snippet: json({ mcpServers: { futureagi: cursor } }),
      deepLink: `cursor://anysphere.cursor-deeplink/mcp/install?name=futureagi&config=${encodeURIComponent(btoa(JSON.stringify(cursor)))}`,
      note: "Set FUTUREAGI_KEY in the environment available to Cursor. Review the install before confirming.",
      docs: "https://docs.cursor.com/context/model-context-protocol",
    },
    {
      id: "vscode",
      name: "VS Code",
      endpoint,
      format: "json",
      snippet: json({ servers: { futureagi: vscode } }),
      deepLink: `vscode:mcp/install?${encodeURIComponent(JSON.stringify({ name: "futureagi", ...vscode }))}`,
      note: "Save in .vscode/mcp.json. Set FUTUREAGI_KEY in the environment available to VS Code.",
      docs: "https://code.visualstudio.com/docs/copilot/customization/mcp-servers",
    },
    {
      id: "codex",
      name: "Codex",
      endpoint,
      format: "toml",
      snippet: `[mcp_servers.futureagi]\nurl = ${JSON.stringify(endpoint)}\nbearer_token_env_var = "FUTUREAGI_KEY"`,
      note: "Add to ~/.codex/config.toml and export FUTUREAGI_KEY before starting Codex.",
      docs: "https://developers.openai.com/codex/mcp",
    },
    {
      id: "chatgpt",
      name: "ChatGPT",
      endpoint,
      format: "url",
      snippet: endpoint,
      note: "Create a custom app in developer mode using this URL. Requires client OAuth sign-in and the appropriate ChatGPT plan; do not put a gateway key in the URL.",
      docs: "https://platform.openai.com/docs/guides/developer-mode",
    },
    {
      id: "responses",
      name: "OpenAI Responses API",
      endpoint,
      format: "json",
      snippet: json({
        tools: [
          {
            type: "mcp",
            server_label: "futureagi",
            server_url: endpoint,
            authorization: "${FUTUREAGI_KEY}",
            require_approval: "always",
          },
        ],
      }),
      note: "This is the tools fragment of a Responses request. Your application must replace the authorization placeholder from its environment; JSON itself does not interpolate variables.",
      docs: "https://platform.openai.com/docs/guides/tools-remote-mcp",
    },
    {
      id: "gemini",
      name: "Gemini CLI",
      endpoint,
      format: "json",
      snippet: json({
        mcpServers: {
          futureagi: {
            httpUrl: endpoint,
            headers: { Authorization: "Bearer ${FUTUREAGI_KEY}" },
          },
        },
      }),
      note: "Add to .gemini/settings.json and export FUTUREAGI_KEY in your shell.",
      docs: "https://geminicli.com/docs/tools/mcp-server/",
    },
    {
      id: "omega",
      name: "Omega",
      endpoint,
      format: "url",
      snippet: endpoint,
      note: "Gateway URL only. The M33 client shim and agentcc-switch installer are not qualified on this release. No automatic install is offered.",
      docs: null,
    },
  ];
}

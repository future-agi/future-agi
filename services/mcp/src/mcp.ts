import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { executeApiTool } from "./api-client.js";
import type { AppConfig } from "./config.js";
import { generatedToolSchemas } from "./generated/schemas.generated.js";
import { generatedTools } from "./generated/tools.generated.js";
import { selectTools, type ToolSelection } from "./tool-selection.js";
import type { ApiCredentials } from "./types.js";

function result(value: unknown, isError = false) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }],
    ...(isError ? { isError: true } : {}),
  };
}

export function createMcpServer(
  credentials: ApiCredentials,
  config: AppConfig,
  fetchImplementation: typeof fetch = fetch,
  selection?: ToolSelection,
): McpServer {
  const server = new McpServer({ name: "FutureAGI", version: "0.1.0" });

  for (const tool of selectTools(generatedTools, selection)) {
    const schema = generatedToolSchemas[tool.name];
    if (!schema) throw new Error(`Generated schema is missing for ${tool.name}`);

    server.registerTool(
      tool.name,
      {
        title: tool.title,
        description: tool.description,
        inputSchema: schema,
        annotations: {
          readOnlyHint: tool.risk === "read",
          destructiveHint: tool.risk === "destructive",
        },
      },
      async (arguments_) => {
        try {
          const parsed = schema.parse(arguments_);
          const response = await executeApiTool(tool, parsed, credentials, {
            baseUrl: config.apiBaseUrl,
            timeoutMs: config.apiTimeoutMs,
            maxResponseBytes: config.maxResponseBytes,
            fetchImplementation,
          });
          return result(response);
        } catch (error) {
          const message = error instanceof Error ? error.message : "Tool execution failed";
          const details =
            error && typeof error === "object" && "details" in error
              ? error.details
              : undefined;
          return result(
            { error: message, ...(details === undefined ? {} : { details }) },
            true,
          );
        }
      },
    );
  }

  return server;
}

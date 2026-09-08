import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { Hono, type Context } from "hono";
import { requestId } from "hono/request-id";
import { secureHeaders } from "hono/secure-headers";

import { AuthenticationError, credentialsFromHeaders } from "./auth.js";
import type { AppConfig } from "./config.js";
import { createMcpServer } from "./mcp.js";
import { selectionFromUrl, ToolSelectionError } from "./tool-selection.js";

export function createApp(config: AppConfig): Hono {
  const app = new Hono();
  app.use("*", requestId(), secureHeaders());
  app.use("*", async (context, next) => {
    if (context.req.path !== "/health" && config.allowedHosts.size > 0) {
      const host = context.req.header("host")?.toLowerCase() ?? "";
      const hostname = host
        .replace(/^\[/u, "")
        .replace(/\](?::\d+)?$/u, "")
        .replace(/:\d+$/u, "");
      if (!config.allowedHosts.has(host) && !config.allowedHosts.has(hostname)) {
        return context.json({ error: "Host is not allowed" }, 403);
      }
    }
    await next();
  });

  app.get("/health", (context) =>
    context.json({ status: "ok", service: "futureagi-mcp", mode: "direct-tools" }),
  );

  const handleMcp = async (context: Context) => {
    try {
      const credentials = credentialsFromHeaders(context.req.raw.headers);
      const selection = selectionFromUrl(new URL(context.req.url));
      const transport = new WebStandardStreamableHTTPServerTransport({
        enableJsonResponse: true,
      });
      const server = createMcpServer(credentials, config, fetch, selection);
      await server.connect(transport);
      return await transport.handleRequest(context.req.raw);
    } catch (error) {
      if (error instanceof AuthenticationError) {
        return context.json(
          { jsonrpc: "2.0", error: { code: -32_001, message: error.message }, id: null },
          401,
          { "WWW-Authenticate": "Bearer" },
        );
      }
      if (error instanceof ToolSelectionError) {
        return context.json(
          { jsonrpc: "2.0", error: { code: -32_602, message: error.message }, id: null },
          400,
        );
      }
      throw error;
    }
  };

  app.all("/mcp", handleMcp);
  app.notFound((context) => context.json({ error: "Not found" }, 404));
  app.onError((error, context) => {
    console.error("mcp_request_failed", {
      requestId: context.get("requestId"),
      error,
    });
    return context.json({ error: "Internal server error" }, 500);
  });
  return app;
}

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApp } from "../src/app.js";
import { createMcpServer } from "../src/mcp.js";
import type { ApiCredentials } from "../src/types.js";

const credentials: ApiCredentials = { apiKey: "public", secretKey: "secret" };
const config = {
  port: 3001,
  apiBaseUrl: "https://api.example.test",
  apiTimeoutMs: 1_000,
  maxResponseBytes: 10_000,
  allowedHosts: new Set<string>(),
};

describe("direct MCP tool surface", () => {
  const closeables: Array<{ close(): Promise<void> }> = [];
  afterEach(async () => {
    await Promise.all(closeables.splice(0).map((item) => item.close()));
  });

  it("exposes every enabled API operation as a direct tool", async () => {
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createMcpServer(credentials, config);
    const client = new Client({ name: "test-client", version: "1.0.0" });
    closeables.push(client, server);
    await server.connect(serverTransport);
    await client.connect(clientTransport);

    const listed = await client.listTools();
    expect(listed.tools.map((tool) => tool.name).sort()).toEqual([
      "list_datasets",
      "list_evaluation_groups",
      "list_traces",
      "list_tracing_projects",
    ]);
    expect(listed.tools.map((tool) => tool.name)).not.toContain(
      "execute_operation",
    );
  });

  it("validates with generated Zod and calls the selected API directly", async () => {
    const fetchImplementation = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ results: [{ name: "billing" }] }),
    );
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createMcpServer(credentials, config, fetchImplementation);
    const client = new Client({ name: "test-client", version: "1.0.0" });
    closeables.push(client, server);
    await server.connect(serverTransport);
    await client.connect(clientTransport);

    const response = await client.callTool({
      name: "list_datasets",
      arguments: { search_text: "billing", page_size: 5 },
    });

    expect(response.isError).not.toBe(true);
    expect(JSON.stringify(response.content)).toContain("billing");
    expect(fetchImplementation).toHaveBeenCalledOnce();
  });

  it("rejects unknown arguments before any API request", async () => {
    const fetchImplementation = vi.fn<typeof fetch>();
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createMcpServer(credentials, config, fetchImplementation);
    const client = new Client({ name: "test-client", version: "1.0.0" });
    closeables.push(client, server);
    await server.connect(serverTransport);
    await client.connect(clientTransport);

    const response = await client.callTool({
      name: "list_datasets",
      arguments: { invented_argument: true },
    });

    expect(response.isError).toBe(true);
    expect(fetchImplementation).not.toHaveBeenCalled();
  });

  it("rejects unauthenticated HTTP requests", async () => {
    const response = await createApp(config).request("/mcp", { method: "POST" });
    expect(response.status).toBe(401);
  });
});

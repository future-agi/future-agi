// A minimal MCP server over stdio (newline-delimited JSON-RPC) with one tool, `greet`.
import { createInterface } from "node:readline";

const TOOLS = [{ name: "greet", description: "Greet someone by name.", inputSchema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } }];

function reply(id, result) {
  process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`);
}

createInterface({ input: process.stdin }).on("line", (line) => {
  const message = JSON.parse(line);
  if (message.id === undefined) return;
  if (message.method === "initialize") reply(message.id, { protocolVersion: "2025-06-18", capabilities: { tools: {} }, serverInfo: { name: "greeter", version: "1.0.0" } });
  else if (message.method === "tools/list") reply(message.id, { tools: TOOLS });
  else if (message.method === "tools/call") reply(message.id, { content: [{ type: "text", text: `Hello, ${message.params?.arguments?.name ?? "there"}.` }] });
  else process.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "method not found" } })}\n`);
});

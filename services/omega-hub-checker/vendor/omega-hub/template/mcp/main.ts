// Starts the MCP server as a child process, lists its tools and calls one, the way an MCP client does.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

export async function callServer(serverFile = new URL("./server.mjs", import.meta.url)) {
  const child = spawn(process.execPath, [fileURLToPath(serverFile)], { stdio: ["pipe", "pipe", "inherit"] });
  const lines = createInterface({ input: child.stdout! })[Symbol.asyncIterator]();
  let next = 1;
  const request = async (method: string, params: unknown = {}) => {
    child.stdin!.write(`${JSON.stringify({ jsonrpc: "2.0", id: next++, method, params })}\n`);
    return JSON.parse((await lines.next()).value as string).result;
  };
  await request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "hub-demo", version: "1.0.0" } });
  const tools = (await request("tools/list")).tools as { name: string }[];
  const called = await request("tools/call", { name: "greet", arguments: { name: "Ada" } });
  child.kill();
  return { tools: tools.map((tool) => tool.name), text: called.content[0].text as string };
}

if (process.argv[1] === import.meta.filename) {
  const { tools, text } = await callServer();
  console.log(`tools: ${tools.join(", ")}`);
  console.log(`greet: ${text}`);
}

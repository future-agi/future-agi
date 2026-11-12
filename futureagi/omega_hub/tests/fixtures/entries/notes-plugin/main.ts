// Starts the plugin's MCP server the way a client does: the manifest says which file to run and which
// environment variable the plugin config sets. Saves two notes, lists them and shows the file.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";

const pluginRoot = path.join(import.meta.dirname, "plugin");

export function readManifest() {
  return JSON.parse(readFileSync(path.join(pluginRoot, "omega.plugin.json"), "utf8")) as {
    id: string;
    version: string;
    config: { name: string; required: boolean }[];
    contributes: { mcpServers: { name: string; args: string[]; tools: string[] }[] };
  };
}

export async function useNotes(notes = ["Buy oat milk", "Call the plumber on Monday"]) {
  const manifest = readManifest();
  const server = manifest.contributes.mcpServers[0]!;
  const file = path.join(mkdtempSync(path.join(tmpdir(), "notes-")), "notes.json");
  const script = server.args[0]!.replace("${pluginRoot}", pluginRoot);
  const child = spawn(process.execPath, [script], { stdio: ["pipe", "pipe", "inherit"], env: { NOTES_FILE: file, PATH: process.env.PATH ?? "" } });
  const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  let next = 1;
  const request = async (method: string, params: unknown = {}) => {
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: next++, method, params })}\n`);
    return JSON.parse((await lines.next()).value as string).result;
  };
  try {
    await request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "hub-demo", version: "1.0.0" } });
    const tools = ((await request("tools/list")).tools as { name: string }[]).map((entry) => entry.name);
    const saved: string[] = [];
    for (const text of notes) saved.push((await request("tools/call", { name: "add_note", arguments: { text } })).content[0].text);
    const listed = (await request("tools/call", { name: "list_notes", arguments: {} })).structuredContent.notes as string[];
    return { manifest, tools, saved, listed, onDisk: JSON.parse(readFileSync(file, "utf8")) as string[] };
  } finally {
    child.kill();
  }
}

if (process.argv[1] === import.meta.filename) {
  const { manifest, tools, saved, listed, onDisk } = await useNotes();
  console.log(`plugin ${manifest.id} ${manifest.version} asks for: ${manifest.config.map((entry) => `${entry.name}${entry.required ? " (required)" : ""}`).join(", ")}`);
  console.log(`server tools: ${tools.join(", ")}`);
  for (const line of saved) console.log(`add_note -> ${line}`);
  console.log(`list_notes -> ${JSON.stringify(listed)}`);
  console.log(`the notes file holds ${onDisk.length} notes`);
}

// A small MCP server: newline-delimited JSON-RPC 2.0 over stdio, no dependencies.
// Tools: add_note { text } appends a note, list_notes {} returns every note.
// Notes live in the JSON file named by NOTES_FILE, or in memory when it is not set.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";

const NOTES_FILE = process.env.NOTES_FILE;
let memory = [];
const load = () => (NOTES_FILE && existsSync(NOTES_FILE) ? JSON.parse(readFileSync(NOTES_FILE, "utf8")) : memory);
const save = (notes) => {
  if (NOTES_FILE) writeFileSync(NOTES_FILE, `${JSON.stringify(notes, null, 2)}\n`);
  else memory = notes;
};

const TOOLS = [
  { name: "add_note", description: "Save a short note.", inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"], additionalProperties: false }, annotations: { destructiveHint: true } },
  { name: "list_notes", description: "List every saved note, oldest first.", inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true } }
];

const text = (value, isError = false) => ({ content: [{ type: "text", text: value }], ...(isError ? { isError: true } : {}) });

function callTool(name, args) {
  if (name === "add_note") {
    const note = String(args.text ?? "").trim();
    if (note === "") return text("note text is empty", true);
    const notes = [...load(), note];
    save(notes);
    return text(`saved note ${notes.length}`);
  }
  if (name === "list_notes") return { content: [{ type: "text", text: JSON.stringify(load()) }], structuredContent: { notes: load() } };
  return undefined;
}

const send = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);

createInterface({ input: process.stdin }).on("line", (line) => {
  const message = JSON.parse(line);
  if (message.id === undefined) return;
  const { id, method, params } = message;
  if (method === "initialize") return send({ jsonrpc: "2.0", id, result: { protocolVersion: "2025-06-18", capabilities: { tools: {} }, serverInfo: { name: "notes", version: "1.2.0" } } });
  if (method === "tools/list") return send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
  if (method === "tools/call") {
    const result = callTool(params?.name, params?.arguments ?? {});
    return send(result ? { jsonrpc: "2.0", id, result } : { jsonrpc: "2.0", id, error: { code: -32602, message: `unknown tool ${params?.name}` } });
  }
  send({ jsonrpc: "2.0", id, error: { code: -32601, message: "method not found" } });
});

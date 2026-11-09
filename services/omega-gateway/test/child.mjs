// Run the real gateway entry point (`node src/server.mjs`) as a child process on loopback, for
// the tests that need its own memory, signals and exit: piping (LD-05) and drain (CH-02).
import { spawn, execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { suiteJwksFile } from "./helpers.mjs";

const ENTRY = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "server.mjs");
// The product wall trusts only claims the gateway verified itself (READINESS 3.13), so the child
// gets the test issuer's public key unless a test names its own.
const JWKS = suiteJwksFile();

export async function startChildGateway(t, env) {
  const child = spawn(process.execPath, [ENTRY], {
    env: { PATH: process.env.PATH, OMEGA_GATEWAY_MODE: "local", PORT: "0", OMEGA_ASSERTION_JWKS_FILE: JWKS, ...(process.env.CHILD_NODE_OPTIONS ? { NODE_OPTIONS: process.env.CHILD_NODE_OPTIONS } : {}), ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const lines = [];
  let buffer = "";
  const listeners = [];
  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString("utf8");
    let index;
    while ((index = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      try {
        const record = JSON.parse(line);
        lines.push(record);
        for (const listener of listeners) listener(record);
      } catch {}
    }
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal, at: performance.now() })));
  t.after(() => { if (child.exitCode === null) child.kill("SIGKILL"); });
  const listening = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`gateway did not start: ${stderr}`)), 10_000);
    listeners.push((record) => {
      if (record.event === "omega-gateway.listening" && record.listener === "app") {
        clearTimeout(timer);
        resolve(record);
      }
    });
    child.once("exit", () => reject(new Error(`gateway exited: ${stderr}`)));
  });
  return {
    child,
    base: listening.url,
    lines,
    exited,
    rssKb: () => Number(execFileSync("ps", ["-o", "rss=", "-p", String(child.pid)]).toString().trim()),
    waitFor: (predicate, ms = 15_000) => new Promise((resolve, reject) => {
      const found = lines.find(predicate);
      if (found) return resolve(found);
      const timer = setTimeout(() => reject(new Error("log line not seen")), ms);
      listeners.push((record) => { if (predicate(record)) { clearTimeout(timer); resolve(record); } });
    }),
  };
}

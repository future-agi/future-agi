// Every way out that the OURS audit (§6.1, §6.12) saw get past the keyless guard. Each probe aims at
// TEST-NET-1 (192.0.2.0/24, RFC 5737), which is never routed on the internet, so even a broken
// sandbox would contact nobody; a working one has no route at all.
import { execFileSync, spawnSync } from "node:child_process";
import dns from "node:dns";
import net from "node:net";
import { Worker } from "node:worker_threads";

const TARGET = "192.0.2.1";

async function attempt(name, run) {
  try {
    await run();
    return { name, reached: true };
  } catch (error) {
    return { name, reached: false, code: error?.code ?? error?.cause?.code ?? "error" };
  }
}

const connect = () => new Promise((resolve, reject) => {
  const socket = net.connect(80, TARGET);
  socket.setTimeout(2000);
  socket.on("connect", () => { socket.destroy(); resolve(); });
  socket.on("error", reject);
  socket.on("timeout", () => { socket.destroy(); reject(Object.assign(new Error("timeout"), { code: "ETIMEDOUT" })); });
});

const inWorker = () => new Promise((resolve, reject) => {
  const source = `const net = require("node:net"); const { parentPort } = require("node:worker_threads");
    const s = net.connect(80, "${TARGET}"); s.setTimeout(2000);
    s.on("connect", () => { parentPort.postMessage("connected"); s.destroy(); });
    s.on("error", (e) => parentPort.postMessage("error:" + e.code));
    s.on("timeout", () => { parentPort.postMessage("error:ETIMEDOUT"); s.destroy(); });`;
  let worker;
  try {
    worker = new Worker(source, { eval: true });
  } catch (error) {
    reject(error);
    return;
  }
  worker.on("message", (message) => {
    worker.terminate();
    if (message === "connected") resolve();
    else reject(Object.assign(new Error(message), { code: String(message).slice(6) }));
  });
  worker.on("error", reject);
});

export async function runProbes() {
  dns.setServers(["192.0.2.53"]);
  return [
    await attempt("fetch", () => fetch(`http://${TARGET}/`, { signal: AbortSignal.timeout(2000) })),
    await attempt("net.connect", connect),
    await attempt("net.connect in a Worker", inWorker),
    await attempt("dns.resolve4", () => dns.promises.resolve4("example.com")),
    await attempt("curl", () => execFileSync("curl", ["-sS", "--max-time", "2", `http://${TARGET}/`], { stdio: "pipe" })),
    await attempt("node child", () => {
      const out = spawnSync(process.execPath, ["-e", `require("node:net").connect(80, "${TARGET}").on("connect", () => process.exit(0)).on("error", () => process.exit(3)).setTimeout(2000, () => process.exit(4))`]);
      if (out.status !== 0) throw Object.assign(new Error("child failed"), { code: `exit-${out.status}` });
    }),
  ];
}

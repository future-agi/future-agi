// Flow-local transport, following datasets/create-upload-ports.mjs (5d478a5c41).
// Keep the managed Docker network internal; expose only fixture ports on loopback.
import net from "node:net";
import { execFileSync, spawn } from "node:child_process";

const project = process.env.E2E_COMPOSE_PROJECT;
const [command, ...args] = process.argv.slice(2);
if (
  process.platform !== "linux" ||
  !command ||
  !/^paramloss-r7(?:-[a-z0-9-]+)?$/.test(project || "")
) {
  throw new Error(
    "Requires a Linux proof VM, paramloss-r7 project and command",
  );
}
const network = `${project}_default`;
const internal = execFileSync(
  "docker",
  ["network", "inspect", network, "--format", "{{.Internal}}"],
  { encoding: "utf8" },
).trim();
if (internal !== "true")
  throw new Error("Fixture network must remain internal");

// e2e/stack/docker-compose.e2e.yml and lib/env.ts pin the host/service ports.
const services = [
  ["frontend", 3100, 80],
  ["backend", 8100, 80],
  ["postgres", 25432, 5432],
  ["clickhouse", 28123, 8123],
];
const servers = [];
const sockets = new Set();
const close = () => {
  for (const socket of sockets) socket.destroy();
  for (const server of servers) server.close();
};

async function preflight() {
  for (const url of [
    "http://localhost:8100/health/",
    "http://localhost:3100/",
  ]) {
    const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!response.ok) throw new Error(`Fixture health failed: ${url}`);
    await response.body?.cancel();
  }
  // PostgreSQL SSLRequest proves this is Postgres, not just an open TCP listener.
  await new Promise((resolve, reject) => {
    const socket = net.connect({ host: "localhost", port: 25432 });
    socket.setTimeout(10_000, () =>
      socket.destroy(new Error("Postgres timeout")),
    );
    socket.once("error", reject);
    socket.once("connect", () =>
      socket.write(Buffer.from([0, 0, 0, 8, 4, 210, 22, 47])),
    );
    socket.once("data", (data) => {
      socket.destroy();
      if (data[0] === 78 || data[0] === 83) resolve();
      else reject(new Error("Unexpected Postgres SSL negotiation response"));
    });
    socket.once("end", () =>
      reject(new Error("Postgres closed before response")),
    );
  });
  console.log("Preflight passed: localhost API, UI and PostgreSQL reachable");
}

try {
  for (const [service, localPort, targetPort] of services) {
    const networks = JSON.parse(
      execFileSync(
        "docker",
        [
          "inspect",
          `${project}-${service}-1`,
          "--format",
          "{{json .NetworkSettings.Networks}}",
        ],
        { encoding: "utf8" },
      ),
    );
    const host = networks[network]?.IPAddress;
    if (!host || net.isIP(host) !== 4)
      throw new Error(`Missing fixture address for ${service}`);
    for (const bind of ["127.0.0.1", "::1"]) {
      const server = net.createServer((client) => {
        const upstream = net.connect({ host, port: targetPort });
        for (const socket of [client, upstream]) {
          sockets.add(socket);
          socket.once("close", () => sockets.delete(socket));
        }
        client.on("error", () => upstream.destroy());
        upstream.on("error", () => client.destroy());
        client.once("close", () => upstream.destroy());
        upstream.once("close", () => client.destroy());
        client.pipe(upstream).pipe(client);
      });
      servers.push(server);
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen({ host: bind, port: localPort }, resolve);
      });
    }
  }
  // No browser command starts until all three required services are reachable.
  await preflight();
  const child = spawn(command, args, { stdio: "inherit" });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      child.kill(signal);
      close();
    });
  }
  process.exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code === 0 ? 0 : 1));
  });
} finally {
  close();
}

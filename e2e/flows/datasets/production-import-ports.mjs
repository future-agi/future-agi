// Loopback access to the upload flow's internal-only Docker network.
// Docker's internal bridge does not publish its declared host ports. Keep that
// isolation and forward only the fixture services this browser flow reads.
import net from "node:net";
import { execFileSync, spawn } from "node:child_process";

const project = "dsx-create-upload";
const network = `${project}_default`;
const [command, ...args] = process.argv.slice(2);
if (process.platform !== "linux" || !command) {
  throw new Error("This helper requires a Linux execution VM and a command");
}
const internal = execFileSync(
  "docker",
  ["network", "inspect", network, "--format", "{{.Internal}}"],
  { encoding: "utf8" },
).trim();
if (internal !== "true")
  throw new Error("The upload fixture network must be internal");

const services = [
  ["frontend", 3100, 80],
  ["backend", 8100, 80],
  ["postgres", 25432, 5432],
  ["clickhouse", 28123, 8123],
  ["fi-collector", 24318, 4318],
];
const servers = [];
const sockets = new Set();
const close = () => {
  for (const socket of sockets) socket.destroy();
  for (const server of servers) server.close();
};
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
  const child = spawn(command, args, { stdio: "inherit" });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
      child.kill(signal);
      close();
    });
  }
  process.exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
} finally {
  close();
}

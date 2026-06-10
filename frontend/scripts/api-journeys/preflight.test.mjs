import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const execFileAsync = promisify(execFile);

describe("api journey preflight CLI", () => {
  const cleanup = [];

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      if (item.type === "server") {
        await new Promise((resolve) => item.server.close(resolve));
      } else if (item.type === "dir") {
        await fs.rm(item.path, { force: true, recursive: true });
      }
    }
  });

  it("fails on API 5xx and preserves token-file auth diagnostics", async () => {
    const api = await startApiServer((request, response) => {
      if (
        request.url === "/accounts/user-info/" ||
        request.url === "/tracer/v1/health"
      ) {
        response.writeHead(500, { "Content-Type": "text/plain" });
        response.end("Internal server error");
        return;
      }

      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
    });
    cleanup.push({ type: "server", server: api.server });

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "preflight-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const tokenFile = path.join(tempDir, "token.json");
    const jsonPath = path.join(tempDir, "preflight.json");
    await fs.writeFile(
      tokenFile,
      `${JSON.stringify({ api_base: api.baseUrl, access: "cached-token" })}\n`,
    );

    const dockerBin = await writeDockerStub(tempDir);
    const result = await runPreflightExpectingFailure({
      apiBase: api.baseUrl,
      dockerBin,
      jsonPath,
      tokenFile,
    });
    const summary = JSON.parse(result.stdout);
    const persisted = JSON.parse(await fs.readFile(jsonPath, "utf8"));

    expect(persisted).toMatchObject({
      api_base: api.baseUrl,
      status: "failed",
    });

    expect(summary.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "api_reachable",
          status: "failed",
          http_status: 500,
          body: "Internal server error",
        }),
        expect.objectContaining({
          name: "api_health",
          status: "failed",
          http_status: 500,
          body: "Internal server error",
        }),
        expect.objectContaining({
          name: "auth_context",
          source: "token_file",
          status: "failed",
          http_status: 500,
          body: "Internal server error",
        }),
        expect.objectContaining({
          name: "docker_disk",
          status: "passed",
        }),
      ]),
    );

    const authContext = summary.checks.find(
      (check) => check.name === "auth_context",
    );
    expect(authContext.detail).toContain("Cached access token");
    expect(authContext.detail).toContain("No FUTURE_AGI_EMAIL");
  });

  for (const publicFlag of ["--public", "--public-only"]) {
    it(`${publicFlag} checks only API reachability and public health`, async () => {
      const api = await startApiServer((request, response) => {
        if (request.url === "/accounts/user-info/") {
          response.writeHead(401, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ detail: "Authentication required" }));
          return;
        }

        if (request.url === "/tracer/v1/health") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(
            JSON.stringify({
              status: "healthy",
              service: "otlp-trace-receiver",
            }),
          );
          return;
        }

        response.writeHead(404, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "not found" }));
      });
      cleanup.push({ type: "server", server: api.server });

      const tempDir = await fs.mkdtemp(
        path.join(os.tmpdir(), "preflight-cli-"),
      );
      cleanup.push({ type: "dir", path: tempDir });
      const jsonPath = path.join(tempDir, "preflight-public.json");

      const result = await execFileAsync(
        process.execPath,
        ["scripts/api-journeys/preflight.mjs", publicFlag, "--json", jsonPath],
        {
          cwd: process.cwd(),
          env: {
            ...process.env,
            API_BASE: api.baseUrl,
            FUTURE_AGI_ACCESS_TOKEN: "",
            FUTURE_AGI_TOKEN_FILE: "",
            FUTURE_AGI_EMAIL: "",
            FUTURE_AGI_PASSWORD: "",
          },
        },
      );
      const summary = JSON.parse(result.stdout);
      const persisted = JSON.parse(await fs.readFile(jsonPath, "utf8"));

      expect(summary).toMatchObject({
        status: "passed",
        mode: "public",
        api_base: api.baseUrl,
      });
      expect(summary.checks.map((check) => check.name)).toEqual([
        "api_reachable",
        "api_health",
      ]);
      expect(persisted).toMatchObject(summary);
    });
  }

  it("fails full preflight when host disk is exhausted", async () => {
    const api = await startApiServer((request, response) => {
      if (request.url === "/accounts/user-info/") {
        if (request.headers.authorization === "Bearer cached-token") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ id: "user-1", email: "a@b.test" }));
          return;
        }

        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Authentication required" }));
        return;
      }

      if (request.url === "/tracer/v1/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "healthy" }));
        return;
      }

      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
    });
    cleanup.push({ type: "server", server: api.server });

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "preflight-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const tokenFile = path.join(tempDir, "token.json");
    const jsonPath = path.join(tempDir, "preflight.json");
    await fs.writeFile(
      tokenFile,
      `${JSON.stringify({ api_base: api.baseUrl, access: "cached-token" })}\n`,
    );

    const dockerBin = await writeDockerStub(tempDir);
    await writeDfStub(tempDir, {
      availableKib: 524288,
      capacityPercent: 100,
    });
    const result = await runPreflightExpectingFailure({
      apiBase: api.baseUrl,
      dockerBin,
      jsonPath,
      tokenFile,
      extraEnv: {
        API_JOURNEY_DB_CONTAINER: "pg",
        API_JOURNEY_HOST_DISK_PATHS: "/workspace,/tmp",
      },
    });
    const summary = JSON.parse(result.stdout);

    expect(summary).toMatchObject({
      status: "failed",
      api_base: api.baseUrl,
    });
    expect(summary.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "auth_context",
          source: "token_file",
          status: "passed",
        }),
        expect.objectContaining({
          name: "host_disk",
          status: "failed",
          paths: ["/workspace", "/tmp"],
        }),
        expect.objectContaining({
          name: "postgres_query:pg",
          status: "passed",
        }),
        expect.objectContaining({
          name: "docker_disk",
          status: "passed",
        }),
      ]),
    );

    const hostDisk = summary.checks.find((check) => check.name === "host_disk");
    expect(hostDisk.filesystems[0]).toMatchObject({
      available_kib: 524288,
      capacity_percent: 100,
    });
    expect(hostDisk.detail).toContain("false route failures");
  });

  it("fails when the Postgres container cannot serve a simple query", async () => {
    const api = await startApiServer((request, response) => {
      if (request.url === "/accounts/user-info/") {
        if (request.headers.authorization === "Bearer cached-token") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ id: "user-1", email: "a@b.test" }));
          return;
        }

        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Authentication required" }));
        return;
      }

      if (request.url === "/tracer/v1/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "healthy" }));
        return;
      }

      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
    });
    cleanup.push({ type: "server", server: api.server });

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "preflight-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const tokenFile = path.join(tempDir, "token.json");
    const jsonPath = path.join(tempDir, "preflight.json");
    await fs.writeFile(
      tokenFile,
      `${JSON.stringify({ api_base: api.baseUrl, access: "cached-token" })}\n`,
    );

    const dockerBin = await writeDockerStub(tempDir, {
      postgresQueryFailure: true,
    });
    const result = await runPreflightExpectingFailure({
      apiBase: api.baseUrl,
      dockerBin,
      jsonPath,
      tokenFile,
      extraEnv: { API_JOURNEY_DB_CONTAINER: "pg" },
    });
    const summary = JSON.parse(result.stdout);

    expect(summary.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "auth_context",
          source: "token_file",
          status: "passed",
        }),
        expect.objectContaining({
          name: "postgres_volume_integrity:pg",
          status: "passed",
        }),
        expect.objectContaining({
          name: "postgres_query:pg",
          status: "failed",
          database: "postgres",
          user: "postgres",
        }),
      ]),
    );

    const queryCheck = summary.checks.find(
      (check) => check.name === "postgres_query:pg",
    );
    expect(queryCheck.stderr).toContain("xlog flush request");
    expect(queryCheck.recent_log_summary.flags).toEqual(
      expect.arrayContaining(["postgres_wal_flush_error", "fatal"]),
    );
  });

  it("fails closed when Docker reports container I/O errors", async () => {
    const api = await startApiServer((request, response) => {
      if (request.url === "/accounts/user-info/") {
        if (request.headers.authorization === "Bearer cached-token") {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ id: "user-1", email: "a@b.test" }));
          return;
        }

        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Authentication required" }));
        return;
      }

      if (request.url === "/tracer/v1/health") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ status: "healthy" }));
        return;
      }

      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
    });
    cleanup.push({ type: "server", server: api.server });

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "preflight-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const tokenFile = path.join(tempDir, "token.json");
    const jsonPath = path.join(tempDir, "preflight.json");
    await fs.writeFile(
      tokenFile,
      `${JSON.stringify({ api_base: api.baseUrl, access: "cached-token" })}\n`,
    );

    const dockerBin = await writeDockerStub(tempDir, {
      dockerIoFailure: true,
    });
    const result = await runPreflightExpectingFailure({
      apiBase: api.baseUrl,
      dockerBin,
      jsonPath,
      tokenFile,
      extraEnv: { API_JOURNEY_DB_CONTAINER: "pg" },
    });
    const summary = JSON.parse(result.stdout);

    expect(summary).toMatchObject({
      status: "failed",
      api_base: api.baseUrl,
    });
    expect(summary.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "auth_context",
          source: "token_file",
          status: "passed",
        }),
        expect.objectContaining({
          name: "postgres_volume_integrity:pg",
          status: "warning",
        }),
        expect.objectContaining({
          name: "postgres_query:pg",
          status: "failed",
          database: "postgres",
          user: "postgres",
        }),
        expect.objectContaining({
          name: "postgres_disk:pg",
          status: "warning",
        }),
        expect.objectContaining({
          name: "docker_disk",
          status: "warning",
        }),
      ]),
    );

    const queryCheck = summary.checks.find(
      (check) => check.name === "postgres_query:pg",
    );
    expect(queryCheck.stderr).toContain("input/output error");
    expect(queryCheck.recent_log_summary.error).toContain("input/output error");

    const dockerDisk = summary.checks.find(
      (check) => check.name === "docker_disk",
    );
    expect(dockerDisk.error).toContain("input/output error");
  });
});

async function startApiServer(handler) {
  const server = http.createServer(handler);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });

  const address = server.address();
  return {
    server,
    baseUrl: `http://127.0.0.1:${address.port}`,
  };
}

async function writeDockerStub(
  tempDir,
  { dockerIoFailure = false, postgresQueryFailure = false } = {},
) {
  const binDir = path.join(tempDir, "bin");
  await fs.mkdir(binDir, { recursive: true });
  const dockerBin = path.join(binDir, "docker");
  await fs.writeFile(
    dockerBin,
    `#!/usr/bin/env node
const args = process.argv.slice(2);
const dockerIoFailure = ${JSON.stringify(dockerIoFailure)};
const postgresQueryFailure = ${JSON.stringify(postgresQueryFailure)};
const pgContainer = {
  State: { Status: "running", Health: { Status: "healthy" }, Error: "" },
  Config: {
    Image: "postgres:16",
    Env: [
      "POSTGRES_USER=postgres",
      "POSTGRES_DB=postgres",
      "PGDATA=/var/lib/postgresql/data",
    ],
  },
  Mounts: [
    {
      Type: "volume",
      Name: "ws2_ws2-database-data",
      Destination: "/var/lib/postgresql/data",
    },
  ],
};

if (args[0] === "system" && args[1] === "df" && args.includes("--format")) {
  if (dockerIoFailure) {
    process.stderr.write("Error response from daemon: failed to retrieve image list: input/output error\\n");
    process.exit(1);
  }
  console.log(JSON.stringify({ Type: "Images", Size: "1GB", Reclaimable: "0B" }));
  console.log(JSON.stringify({ Type: "Local Volumes", Size: "1GB", Reclaimable: "0B" }));
  process.exit(0);
}

if (args[0] === "system" && args[1] === "df" && args[2] === "-v") {
  console.log("Local Volumes space usage:");
  console.log("VOLUME NAME LINKS SIZE");
  console.log("Build cache usage:");
  process.exit(0);
}

if (args[0] === "inspect" && args[1] === "pg" && args.includes("--format")) {
  console.log(JSON.stringify(pgContainer.State));
  process.exit(0);
}

if (args[0] === "inspect" && args[1] === "pg") {
  console.log(JSON.stringify([pgContainer]));
  process.exit(0);
}

if (args[0] === "run" && args.includes("ws2_ws2-database-data:/mnt:ro")) {
  if (dockerIoFailure) {
    process.stderr.write("docker: Error response from daemon: failed to mount volume: input/output error\\n");
    process.exit(125);
  }
  console.log("file\\tPG_VERSION\\tok\\t3");
  console.log("file\\tpostgresql.conf\\tok\\t29950");
  console.log("file\\tpg_hba.conf\\tok\\t198");
  console.log("file\\tpg_ident.conf\\tok\\t2640");
  console.log("file\\tpostgresql.auto.conf\\tok\\t158");
  console.log("dir\\tbase\\tpresent\\t0");
  console.log("dir\\tglobal\\tpresent\\t0");
  console.log("dir\\tpg_wal\\tpresent\\t0");
  console.log("dir\\tpg_xact\\tpresent\\t0");
  console.log("entries\\t.\\t26\\t0");
  process.exit(0);
}

if (args[0] === "exec" && args[1] === "pg" && args[2] === "sh") {
  if (dockerIoFailure) {
    process.stderr.write("exec /usr/bin/sh: input/output error\\n");
    process.exit(126);
  }
  console.log("Filesystem 1024-blocks Used Available Capacity Mounted on");
  console.log("overlay 10000000 100000 9900000 1% /");
  console.log("/dev/vdb1 10000000 100000 9900000 1% /var/lib/postgresql/data");
  process.exit(0);
}

if (args[0] === "exec" && args[1] === "pg" && args.includes("psql")) {
  if (dockerIoFailure) {
    process.stderr.write("exec /usr/bin/psql: input/output error\\n");
    process.exit(126);
  }
  if (postgresQueryFailure) {
    process.stderr.write("FATAL:  xlog flush request 55/A131DE0 is not satisfied --- flushed only to 55/2541658\\n");
    process.exit(2);
  }
  console.log("1");
  process.exit(0);
}

if (args[0] === "logs" && args.includes("pg")) {
  if (dockerIoFailure) {
    process.stderr.write("error from daemon in stream: Error grabbing logs: open container-json.log: input/output error\\n");
    process.exit(1);
  }
  console.log("FATAL:  xlog flush request 55/A131DE0 is not satisfied --- flushed only to 55/2541658");
  console.log("LOG:  request to flush past end of generated WAL");
  process.exit(0);
}

process.stderr.write("stub docker: unsupported command " + args.join(" "));
process.exit(1);
`,
    { mode: 0o755 },
  );
  return dockerBin;
}

async function writeDfStub(
  tempDir,
  { availableKib = 9900000, capacityPercent = 1 } = {},
) {
  const binDir = path.join(tempDir, "bin");
  await fs.mkdir(binDir, { recursive: true });
  const dfBin = path.join(binDir, "df");
  const blocks = 10000000;
  const used = Math.max(0, blocks - availableKib);
  await fs.writeFile(
    dfBin,
    `#!/usr/bin/env node
const args = process.argv.slice(2);
if (args[0] === "-Pk") {
  console.log("Filesystem 1024-blocks Used Available Capacity Mounted on");
  console.log("/dev/disk-test ${blocks} ${used} ${availableKib} ${capacityPercent}% /test-host");
  process.exit(0);
}
process.stderr.write("stub df: unsupported command " + args.join(" "));
process.exit(1);
`,
    { mode: 0o755 },
  );
  return dfBin;
}

async function runPreflightExpectingFailure({
  apiBase,
  dockerBin,
  jsonPath,
  tokenFile,
  extraEnv = {},
}) {
  try {
    await execFileAsync(
      process.execPath,
      ["scripts/api-journeys/preflight.mjs", "--json", jsonPath],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          API_BASE: apiBase,
          FUTURE_AGI_TOKEN_FILE: tokenFile,
          PATH: `${path.dirname(dockerBin)}:${process.env.PATH}`,
          ...extraEnv,
        },
      },
    );
  } catch (error) {
    expect(error.code).toBe(1);
    return error;
  }

  throw new Error("preflight unexpectedly passed");
}

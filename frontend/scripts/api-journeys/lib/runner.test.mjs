import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it, vi } from "vitest";
import { runJourneys } from "./runner.mjs";

describe("runJourneys public journey support", () => {
  const cleanup = [];
  const originalEnv = {
    API_BASE: process.env.API_BASE,
    FUTURE_AGI_TOKEN_FILE: process.env.FUTURE_AGI_TOKEN_FILE,
    PATH: process.env.PATH,
  };

  afterEach(() => {
    vi.restoreAllMocks();
    process.exitCode = undefined;
    restoreEnv();
  });

  it("runs explicitly selected public journeys without authenticated context setup", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const stdoutSpy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);
    const result = await runJourneys(
      [
        {
          id: "PUBLIC-TEST",
          title: "Public test",
          public: true,
          async run({ apiBase, client, tokens, user, evidence }) {
            evidence.push({ apiBase, has_client: Boolean(client) });
            expect(tokens).toEqual({});
            expect(user).toBeNull();
          },
        },
      ],
      ["--only", "PUBLIC-TEST"],
    );

    expect(result.status).toBe("passed");
    expect(result.passed).toBe(1);
    expect(result.results[0].evidence[0]).toMatchObject({
      apiBase: "http://localhost:8003",
      has_client: true,
    });
    expect(process.exitCode).toBeUndefined();
    logSpy.mockRestore();
    stdoutSpy.mockRestore();
  });

  it("fails unknown --only ids instead of passing a zero-journey run", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const stdoutSpy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "runner-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const jsonPath = path.join(tempDir, "summary.json");

    const result = await runJourneys(
      [
        {
          id: "PUBLIC-TEST",
          title: "Public test",
          public: true,
          async run() {
            throw new Error("journey should not run for unknown selection");
          },
        },
      ],
      ["--only", "PUBLIC-TEST,TYPO-API-999", "--json", jsonPath],
    );
    const persisted = JSON.parse(await fs.readFile(jsonPath, "utf8"));

    expect(result).toMatchObject({
      status: "failed",
      failed: 1,
      requested_total: 2,
      selected_total: 0,
    });
    expect(result.results[0]).toMatchObject({
      id: "journey_selection",
      status: "failed",
    });
    expect(result.results[0].error).toContain("TYPO-API-999");
    expect(result.results[0].evidence[0]).toMatchObject({
      requested_ids: ["PUBLIC-TEST", "TYPO-API-999"],
      unknown_ids: ["TYPO-API-999"],
      available_count: 1,
    });
    expect(persisted).toMatchObject(result);
    expect(process.exitCode).toBe(1);
    logSpy.mockRestore();
    stdoutSpy.mockRestore();
  });

  it("stops selected journeys when required preflight reports API 5xx", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const stdoutSpy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);
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

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "runner-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const tokenFile = path.join(tempDir, "token.json");
    const jsonPath = path.join(tempDir, "preflight.json");
    await fs.writeFile(
      tokenFile,
      `${JSON.stringify({ api_base: api.baseUrl, access: "cached-token" })}\n`,
    );
    const dockerBin = await writeDockerStub(tempDir);

    process.env.API_BASE = api.baseUrl;
    process.env.FUTURE_AGI_TOKEN_FILE = tokenFile;
    process.env.PATH = `${path.dirname(dockerBin)}:${originalEnv.PATH || ""}`;

    const result = await runJourneys(
      [
        {
          id: "PUBLIC-TEST",
          title: "Public test",
          public: true,
          async run() {
            throw new Error("journey should not run after failed preflight");
          },
        },
      ],
      [
        "--only",
        "PUBLIC-TEST",
        "--require-preflight",
        "--preflight-json",
        jsonPath,
      ],
    );

    expect(result).toMatchObject({
      status: "failed",
      failed: 1,
      selected_total: 1,
    });
    expect(result.results[0]).toMatchObject({
      id: "preflight",
      status: "failed",
      preflight_json: jsonPath,
    });
    expect(result.results[0].error).toContain("api_reachable");
    expect(result.results[0].error).toContain("api_health");
    expect(result.results[0].evidence[0].checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "api_reachable",
          status: "failed",
          http_status: 500,
        }),
        expect.objectContaining({
          name: "api_health",
          status: "failed",
          http_status: 500,
        }),
      ]),
    );
    expect(process.exitCode).toBe(1);
    logSpy.mockRestore();
    stdoutSpy.mockRestore();
  });

  it("stops public selected journeys when required public preflight reports API 5xx", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const stdoutSpy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);
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

    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "runner-cli-"));
    cleanup.push({ type: "dir", path: tempDir });
    const jsonPath = path.join(tempDir, "public-preflight.json");

    process.env.API_BASE = api.baseUrl;

    const result = await runJourneys(
      [
        {
          id: "PUBLIC-TEST",
          title: "Public test",
          public: true,
          async run() {
            throw new Error(
              "journey should not run after failed public preflight",
            );
          },
        },
      ],
      [
        "--only",
        "PUBLIC-TEST",
        "--require-public-preflight",
        "--preflight-json",
        jsonPath,
      ],
    );
    const persistedPreflight = JSON.parse(await fs.readFile(jsonPath, "utf8"));

    expect(result).toMatchObject({
      status: "failed",
      failed: 1,
      selected_total: 1,
    });
    expect(result.results[0]).toMatchObject({
      id: "preflight",
      title: "API journey public preflight",
      status: "failed",
      preflight_json: jsonPath,
    });
    expect(result.results[0].error).toContain("api_reachable");
    expect(result.results[0].error).toContain("api_health");
    expect(
      result.results[0].evidence[0].checks.map((check) => check.name),
    ).toEqual(["api_reachable", "api_health"]);
    expect(persistedPreflight).toMatchObject({
      status: "failed",
      mode: "public",
      api_base: api.baseUrl,
    });
    expect(process.exitCode).toBe(1);
    logSpy.mockRestore();
    stdoutSpy.mockRestore();
  });

  it("rejects required public preflight when a selected journey is not public", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const stdoutSpy = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);

    const result = await runJourneys(
      [
        {
          id: "PUBLIC-TEST",
          title: "Public test",
          public: true,
          async run() {
            throw new Error("public journey should not run");
          },
        },
        {
          id: "PRIVATE-TEST",
          title: "Private test",
          async run() {
            throw new Error("private journey should not run");
          },
        },
      ],
      ["--only", "PUBLIC-TEST,PRIVATE-TEST", "--require-public-preflight"],
    );

    expect(result).toMatchObject({
      status: "failed",
      failed: 1,
      selected_total: 2,
    });
    expect(result.results[0]).toMatchObject({
      id: "preflight",
      title: "API journey public preflight",
      status: "failed",
    });
    expect(result.results[0].error).toContain("can only be used");
    expect(result.results[0].evidence[0]).toMatchObject({
      non_public_ids: ["PRIVATE-TEST"],
    });
    expect(process.exitCode).toBe(1);
    logSpy.mockRestore();
    stdoutSpy.mockRestore();
  });

  afterEach(async () => {
    for (const item of cleanup.splice(0).reverse()) {
      if (item.type === "server") {
        await new Promise((resolve) => item.server.close(resolve));
      } else if (item.type === "dir") {
        await fs.rm(item.path, { force: true, recursive: true });
      }
    }
  });

  function restoreEnv() {
    for (const [key, value] of Object.entries(originalEnv)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
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

async function writeDockerStub(tempDir) {
  const binDir = path.join(tempDir, "bin");
  await fs.mkdir(binDir, { recursive: true });
  const dockerBin = path.join(binDir, "docker");
  await fs.writeFile(
    dockerBin,
    `#!/usr/bin/env node
const args = process.argv.slice(2);

if (args[0] === "system" && args[1] === "df" && args.includes("--format")) {
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

process.stderr.write("stub docker: unsupported command " + args.join(" "));
process.exit(1);
`,
    { mode: 0o755 },
  );
  return dockerBin;
}

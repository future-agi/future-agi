import assert from "node:assert/strict";
import { once } from "node:events";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { registerHooks } from "node:module";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { request } from "@playwright/test";
import ts from "typescript";
import { parseDocument } from "yaml";
import { checkApiConnectionPolicy } from "./check-api-connection-policy.mjs";

// Match state-probe.test.mjs: exercise the real ApiClient, including its TS
// parameter properties, rather than a copy of its transport/error handling.
const apiModule = new URL("../lib/api-client.ts", import.meta.url).href;
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== apiModule) return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.ESNext,
        },
      }).outputText,
    };
  },
});
const { ApiClient, ApiError } = await import(apiModule);

for (const [file, service] of [
  ["docker-compose.e2e.yml", "backend"],
  ["docker-compose.standalone-e2e.yml", "app"],
]) {
  test(`configuration: ${file} disables Granian HTTP/1 persistence`, () => {
    const document = parseDocument(
      readFileSync(new URL(`../stack/${file}`, import.meta.url), "utf8"),
      {
        customTags: ["!reset", "!override"].map((tag) => ({
          tag,
          collection: "seq",
          resolve: (value) => value,
        })),
      },
    );
    assert.deepEqual(document.errors, []);
    assert.deepEqual(document.warnings, []);
    assert.equal(
      document.toJS().services[service].environment.GRANIAN_HTTP1_KEEP_ALIVE,
      "false",
    );
  });
}

// All sockets and contexts are closed even when an assertion rejects. No sleeps
// or attempts to win a 30-second race: socket events drive the negative cases.
async function fixture(t, handler) {
  const calls = [];
  const sockets = new Set();
  const server = createServer((req, res) => {
    calls.push({ method: req.method, url: req.url, headers: req.headers, socket: req.socket });
    handler(req, res, calls.length);
  });
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    const closed = once(server, "close");
    server.close();
    for (const socket of sockets) socket.destroy();
    await closed;
  });
  return { endpoint: `http://127.0.0.1:${server.address().port}/health/`, calls };
}

function healthy(req, res) {
  res.writeHead(200, { Connection: "close" });
  res.end("healthy");
}

const transportFailures = [
  ["reset before headers", (req) => req.socket.destroy()],
  ...[false, true].map((compressed) => [
    `incomplete ${compressed ? "gzip" : "plain"} response after headers`,
    (req, res) => {
      const body = compressed ? gzipSync("incomplete health response") : Buffer.from("healthy");
      res.writeHead(200, {
        Connection: "close",
        "Content-Length": body.length,
        ...(compressed ? { "Content-Encoding": "gzip" } : {}),
      });
      res.flushHeaders();
      res.write(body.subarray(0, Math.floor(body.length / 2)), () => {
        setImmediate(() => req.socket.destroy());
      });
    },
  ]),
];
const transportError = /socket hang up|aborted|ECONNRESET|premature|decompress|terminated/i;

test("verifier: three complete close responses use distinct sockets", async (t) => {
  const { endpoint, calls } = await fixture(t, healthy);
  const responses = await checkApiConnectionPolicy(endpoint);
  assert.deepEqual(responses.map(({ request, status, connection }) => ({ request, status, connection })), [
    { request: 1, status: 200, connection: "close" },
    { request: 2, status: 200, connection: "close" },
    { request: 3, status: 200, connection: "close" },
  ]);
  assert.equal(calls.length, 3);
  assert.equal(new Set(calls.map((call) => call.socket)).size, 3);
  for (const call of calls) {
    assert.equal(call.method, "GET");
    assert.equal(call.url, "/health/");
    assert.notEqual(call.headers.connection, "close");
  }
});

for (const badResponse of [1, 2, 3]) {
  test(`verifier: rejects persistent response ${badResponse} without polling`, async (t) => {
    const { endpoint, calls } = await fixture(t, (req, res, count) => {
      if (count !== badResponse) return healthy(req, res);
      res.writeHead(200, { Connection: "keep-alive" });
      res.end("healthy");
    });
    await assert.rejects(checkApiConnectionPolicy(endpoint), /must advertise Connection: close/);
    assert.equal(calls.length, badResponse);
  });
}

for (const status of [302, 500]) {
  test(`verifier: rejects HTTP ${status} without following or retrying`, async (t) => {
    const { endpoint, calls } = await fixture(t, (req, res) => {
      res.writeHead(status, { Connection: "close", Location: "/health/" });
      res.end("not healthy");
    });
    await assert.rejects(checkApiConnectionPolicy(endpoint), /backend health status/);
    assert.equal(calls.length, 1);
  });
}

for (const [name, handler] of transportFailures) {
  test(`verifier: ${name} fails after one request`, async (t) => {
    const { endpoint, calls } = await fixture(t, handler);
    await assert.rejects(checkApiConnectionPolicy(endpoint), transportError);
    assert.equal(calls.length, 1);
  });
}

const methods = ["get", "post", "put", "patch", "delete"];
for (const method of methods) {
  test(`ApiClient: ${method.toUpperCase()} reset rejects after one unchanged invocation`, async () => {
    const calls = [];
    const reset = Object.assign(new Error("socket hang up"), { code: "ECONNRESET" });
    const context = {
      [method]: async (...args) => {
        calls.push(args);
        throw reset;
      },
    };
    const client = new ApiClient(context, "http://backend").withAuth(
      { access: "synthetic-access", refresh: "synthetic-refresh" }, "org", "workspace",
    );
    const input = { value: "query or body & unicode 日本" };
    await assert.rejects(client[method]("/resource/", input), (error) => error === reset);
    assert.deepEqual(calls, [["http://backend/resource/", {
      headers: { Authorization: "Bearer synthetic-access", "X-Organization-Id": "org", "X-Workspace-Id": "workspace" },
      params: method === "get" ? input : undefined,
      data: ["post", "put", "patch"].includes(method) ? input : undefined,
    }]]);
  });
}

for (const status of [401, 403, 429, 500, 503]) {
  test(`ApiClient: HTTP ${status} remains an ApiError after one invocation`, async () => {
    let calls = 0;
    const body = { detail: "synthetic failure" };
    const client = new ApiClient({ get: async () => {
      calls++;
      return { text: async () => JSON.stringify(body), status: () => status };
    } }, "http://backend");
    await assert.rejects(client.get("/resource/"), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(error.path, "/resource/");
      assert.deepEqual(error.body, body);
      return true;
    });
    assert.equal(calls, 1);
  });
}

test("ApiClient: response.text failure is not replayed", async () => {
  let calls = 0;
  const failure = new Error("incomplete body");
  const client = new ApiClient({ get: async () => {
    calls++;
    return { text: async () => { throw failure; }, status: () => 200 };
  } }, "http://backend");
  await assert.rejects(client.get("/resource/"), (error) => error === failure);
  assert.equal(calls, 1);
});

test("ApiClient: a wrong caller assertion fails without a second request", async () => {
  let calls = 0;
  const client = new ApiClient({ get: async () => {
    calls++;
    return { text: async () => '{"cost":null}', status: () => 200 };
  } }, "http://backend");
  const body = await client.get("/resource/");
  assert.throws(() => assert.equal(body.cost, 0), assert.AssertionError);
  assert.equal(calls, 1);
});

// Real pinned Playwright, not just a recording context: a future broad
// maxRetries change must not hide either pre-header or partial-body resets.
for (const method of methods) {
  for (const [name, handler] of transportFailures) {
    test(`ApiClient + Playwright: ${method.toUpperCase()} ${name} is never replayed`, async (t) => {
      const { endpoint, calls } = await fixture(t, handler);
      const context = await request.newContext({ timeout: 10_000 });
      t.after(() => context.dispose());
      const client = new ApiClient(context, new URL(endpoint).origin);
      await assert.rejects(client[method]("/health/"), transportError);
      assert.equal(calls.length, 1);
      assert.equal(calls[0].method, method.toUpperCase());
    });
  }
}

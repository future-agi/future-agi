import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import { registerHooks } from "node:module";
import test from "node:test";
import { gzipSync } from "node:zlib";
import { request, test as playwrightTest } from "@playwright/test";
import ts from "typescript";

// As in state-probe.test.mjs, import the actual class (including TS parameter
// properties); only the APIRequestContext and current test info are faked.
const apiModule = new URL("../lib/api-client.ts", import.meta.url).href;
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== apiModule) return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
      }).outputText,
    };
  },
});
const { ApiClient, ApiError } = await import(apiModule);
const response = (status = 200, body = { ok: true }) => ({
  status: () => status, text: async () => JSON.stringify(body),
});
function recordingContext(method, outcomes) {
  const calls = [];
  const context = { [method]: async (...args) => {
    calls.push(args);
    assert.ok(calls.length <= outcomes.length, "unexpected extra request");
    const outcome = outcomes[calls.length - 1];
    if (outcome instanceof Error || typeof outcome === "string") throw outcome;
    return outcome;
  } };
  return { client: new ApiClient(context, "http://backend"), calls };
}
function annotations(t) {
  const entries = [];
  t.mock.method(playwrightTest, "info", () => ({ annotations: entries }));
  return entries;
}

for (const method of ["get", "head"]) {
  for (const message of ["socket hang up", "read ECONNRESET", "write EPIPE"]) {
    test(`${method}: ${message} retries once with identical options and an annotation`, async (t) => {
      const entries = annotations(t);
      const failure = new Error(`apiRequestContext.${method}: ${message}\nCall log:\n  - → GET http://backend/resource/\n    - authorization: Bearer secret-not-for-report`);
      const { client, calls } = recordingContext(method, [failure, response()]);
      const authed = client.withAuth({ access: "synthetic-access", refresh: "synthetic-refresh" }, "org", "workspace");
      const params = { value: "query & unicode 日本" };
      const result = await authed[method]("/resource/", params);
      assert.deepEqual(result, { ok: true });
      assert.deepEqual(calls, Array(2).fill(["http://backend/resource/", {
        headers: { Authorization: "Bearer synthetic-access", "X-Organization-Id": "org", "X-Workspace-Id": "workspace" },
        params, data: undefined, maxRetries: 0,
      }]));
      assert.deepEqual(entries, [{ type: "api-transport-retry", description: `${method.toUpperCase()} /resource/: apiRequestContext.${method}: ${message}` }]);
    });
  }
}

test("two resets fail with both errors and only one annotation", async (t) => {
  const entries = annotations(t);
  const first = new Error("socket hang up"), second = new Error("read ECONNRESET");
  const { client, calls } = recordingContext("get", [first, second]);
  await assert.rejects(client.get("/resource/"), (error) => {
    assert.ok(error instanceof AggregateError);
    assert.deepEqual(error.errors, [first, second]);
    assert.match(error.message, /GET \/resource\/.*first: socket hang up; retry: read ECONNRESET/);
    return true;
  });
  assert.equal(calls.length, 2);
  assert.equal(entries.length, 1);
});

test("retry outside a test warns exactly once", async (t) => {
  // The real Playwright test.info() throws outside a test; do not stub it here.
  const warn = t.mock.method(console, "warn", () => {});
  const { client, calls } = recordingContext("get", [new Error("socket hang up"), response()]);
  await client.get("/resource/");
  assert.equal(calls.length, 2);
  assert.deepEqual(warn.mock.calls.map(call => call.arguments), [["[api-transport-retry] GET /resource/: socket hang up"]]);
});

for (const method of ["post", "put", "patch", "delete"]) {
  test(`${method}: reset fails without replaying a mutation`, async (t) => {
    const entries = annotations(t);
    const reset = new Error("socket hang up");
    const { client, calls } = recordingContext(method, [reset]);
    const data = { value: "unchanged body" };
    await assert.rejects(client[method]("/resource/", data), error => error === reset);
    assert.deepEqual(calls, [["http://backend/resource/", {
      headers: {}, params: undefined, data: method === "delete" ? undefined : data, maxRetries: 0,
    }]]);
    assert.deepEqual(entries, []);
  });
}

for (const [name, failure] of [
  ["timeout", new Error("apiRequestContext.get: Timeout 30000ms exceeded.")],
  ["timeout mentioning a reset", new Error("apiRequestContext.get: request timed out after ECONNRESET")],
  ["DNS", new Error("apiRequestContext.get: getaddrinfo ENOTFOUND backend")],
  ["refused connection", new Error("apiRequestContext.get: connect ECONNREFUSED")],
  ["disposed context", new Error("apiRequestContext.get: Target page, context or browser has been closed")],
  ["reset word only in request URL", new Error("apiRequestContext.get: request failed\nCall log:\n  - → GET http://backend/ECONNRESET")],
  ["non-Error throw", "socket hang up"],
  ["explicit status", Object.assign(new Error("socket hang up"), { status: 500 })],
  ["explicit statusCode", Object.assign(new Error("socket hang up"), { statusCode: 200 })],
  ["response started", new Error("apiRequestContext.get: read ECONNRESET\nCall log:\n  - ← 200 OK\n    - content-encoding: gzip")],
  ["redirect before reset", new Error("apiRequestContext.get: socket hang up\nCall log:\n  - ← 302 Found\n  - → GET http://backend/next")],
]) {
  test(`${name} is not retried or annotated`, async (t) => {
    const entries = annotations(t);
    const { client, calls } = recordingContext("get", [failure]);
    await assert.rejects(client.get("/resource/"), error => error === failure);
    assert.equal(calls.length, 1);
    assert.deepEqual(entries, []);
  });
}

for (const status of [401, 403, 429, 500, 503]) {
  test(`HTTP ${status} remains an ApiError without a retry`, async (t) => {
    const entries = annotations(t), body = { detail: "socket hang up" };
    const { client, calls } = recordingContext("get", [response(status, body)]);
    await assert.rejects(client.get("/resource/"), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(error.path, "/resource/");
      assert.deepEqual(error.body, body);
      return true;
    });
    assert.equal(calls.length, 1);
    assert.deepEqual(entries, []);
  });
}

test("HTTP 500 on the retry is not retried again", async (t) => {
  const entries = annotations(t);
  const { client, calls } = recordingContext("get", [new Error("EPIPE"), response(500)]);
  await assert.rejects(client.get("/resource/"), error => error instanceof ApiError && error.status === 500);
  assert.equal(calls.length, 2);
  assert.equal(entries.length, 1);
});

test("body read reset after an APIResponse is never retried", async (t) => {
  const entries = annotations(t), failure = new Error("ECONNRESET");
  const { client, calls } = recordingContext("get", [{ ...response(), text: async () => { throw failure; } }]);
  await assert.rejects(client.get("/resource/"), error => error === failure);
  assert.equal(calls.length, 1);
  assert.deepEqual(entries, []);
});

test("successful response and a wrong caller assertion do not replay the request", async (t) => {
  const entries = annotations(t);
  const { client, calls } = recordingContext("get", [response(200, { cost: null })]);
  const body = await client.get("/resource/");
  assert.throws(() => assert.equal(body.cost, 0), assert.AssertionError);
  assert.equal(calls.length, 1);
  assert.deepEqual(entries, []);
});

// Qualify the public Playwright error boundary too: a request promise can reject
// AFTER receiving HTTP headers (notably a truncated compressed response).
async function fixture(t, handler) {
  const calls = [], sockets = new Set();
  const server = createServer((req, res) => {
    calls.push({ method: req.method, url: req.url });
    handler(req, res, calls.length);
  });
  server.on("connection", socket => {
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
  const context = await request.newContext({ timeout: 10_000 });
  t.after(() => context.dispose());
  return { client: new ApiClient(context, `http://127.0.0.1:${server.address().port}`), calls };
}

for (const method of ["get", "head", "post", "put", "patch", "delete"]) {
  test(`real Playwright ${method}: reset before headers follows the method policy`, async (t) => {
    const entries = annotations(t);
    const { client, calls } = await fixture(t, (req, res, count) => {
      if (count === 1) return req.socket.destroy();
      res.end('{"ok":true}');
    });
    if (["get", "head"].includes(method)) {
      await client[method]("/resource/");
      assert.equal(calls.length, 2);
      assert.equal(entries.length, 1);
    } else {
      await assert.rejects(client[method]("/resource/"), /socket hang up|ECONNRESET|EPIPE/);
      assert.equal(calls.length, 1);
      assert.deepEqual(entries, []);
    }
    assert.ok(calls.every(call => call.method === method.toUpperCase()));
  });
}

for (const compressed of [false, true]) {
  test(`real Playwright: partial ${compressed ? "gzip" : "plain"} response is not retried`, async (t) => {
    const entries = annotations(t);
    const { client, calls } = await fixture(t, (req, res) => {
      const body = compressed ? gzipSync("incomplete response") : Buffer.from("incomplete response");
      res.writeHead(200, { "Content-Length": body.length, ...(compressed ? { "Content-Encoding": "gzip" } : {}) });
      res.flushHeaders();
      res.write(body.subarray(0, Math.floor(body.length / 2)), () => setImmediate(() => req.socket.destroy()));
    });
    await assert.rejects(client.get("/resource/"), error => {
      assert.match(error.message, /aborted|ECONNRESET|premature|terminated/i);
      assert.match(error.message, /← 200/);
      return true;
    });
    assert.equal(calls.length, 1);
    assert.deepEqual(entries, []);
  });
}

test("real Playwright: redirect response followed by reset is not retried", async (t) => {
  const entries = annotations(t);
  const { client, calls } = await fixture(t, (req, res) => {
    if (req.url === "/next") return req.socket.destroy();
    res.writeHead(302, { Location: "/next" });
    res.end();
  });
  await assert.rejects(client.get("/resource/"), /socket hang up|ECONNRESET/);
  assert.deepEqual(calls.map(call => call.url), ["/resource/", "/next"]);
  assert.deepEqual(entries, []);
});

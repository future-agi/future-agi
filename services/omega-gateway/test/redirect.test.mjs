// Redirects must never replay an admitted request outside the configured upstream.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { createGateway } from "../src/server.mjs";
import { loadConfig } from "../src/config.mjs";
import { createSpanExporter } from "../src/otlp.mjs";
import { startRequest } from "../src/trace.mjs";
import { learnMint, followChannel } from "../src/channel-token/taps.mjs";
import { mintAssertion, writeJwks } from "./helpers.mjs";

async function listen(t, server) {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  );
  return `http://127.0.0.1:${server.address().port}`;
}

async function fixture(
  t,
  { status = 307, location, remotePath = "", basePath = "/omega/api" } = {},
) {
  const arrivals = [];
  const secondArrivals = [];
  const second = await listen(
    t,
    http.createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      secondArrivals.push({
        method: request.method,
        body: Buffer.concat(chunks).toString(),
      });
      response
        .writeHead(200, { "content-type": "application/json" })
        .end('{"followed":true}');
    }),
  );
  let ee;
  ee = await listen(
    t,
    http.createServer(async (request, response) => {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      arrivals.push({
        path: request.url,
        body: Buffer.concat(chunks).toString(),
      });
      // A follow has a finite successful answer rather than a redirect loop.
      const first = arrivals.length === 1;
      const target =
        typeof location === "function" ? location({ ee, second }) : location;
      response
        .writeHead(first ? status : 200, {
          "content-type": "application/json",
          "set-cookie": ["upstream_only=fixture; HttpOnly", "other=fixture"],
          "x-internal-route": "private",
          "x-omega-authority": "private",
          etag: '"receipt"',
          "retry-after": "3",
          "x-omega-operation-id": "op-fixture",
          "x-request-id": "ee-fixture",
          ...(first && target !== undefined ? { location: target } : {}),
        })
        .end('{"upstream":true}');
    }),
  );
  const config = loadConfig({
    OMEGA_GATEWAY_MODE: "local",
    OMEGA_REMOTE_BASE_URL: `${ee}${remotePath}`,
    OMEGA_SESSION_EXCHANGE_URL: `${ee}/exchange`,
    OMEGA_GATEWAY_BASE_PATH: basePath,
    OMEGA_ASSERTION_JWKS_FILE: writeJwks(t),
  });
  const gateway = createGateway({ config, log() {}, refusalLog() {} });
  t.after(() => gateway.rateLimiter.close());
  const base = await listen(t, gateway.app);
  const send = (path = "/workspace/deployments", options = {}) =>
    fetch(`${base}${basePath}${path}`, {
      method: "POST",
      body: "admitted-body-must-not-be-replayed",
      redirect: "manual",
      headers: {
        authorization: `Bearer ${mintAssertion()}`,
        "content-type": "application/json",
      },
      signal: AbortSignal.timeout(5_000),
      ...options,
    });
  return { send, arrivals, secondArrivals, gateway, ee, second };
}

for (const status of [307, 308]) {
  test(`${status}: a different origin receives ZERO requests and no replayed body`, async (t) => {
    const f = await fixture(t, {
      status,
      location: ({ second }) => `${second}/capture`,
    });
    const response = await f.send();
    await response.text();
    assert.equal(f.arrivals.length, 1, "one admitted EE request");
    assert.equal(f.arrivals[0].body, "admitted-body-must-not-be-replayed");
    t.diagnostic(
      `destination requests=${f.secondArrivals.length}; matching replayed bodies=${f.secondArrivals.filter((r) => r.body === f.arrivals[0].body).length}`,
    );
    assert.equal(
      f.secondArrivals.length,
      0,
      "redirect destination must receive ZERO requests/bodies",
    );
    assert.equal(response.status, 502);
  });
}

for (const status of [301, 302, 303, 307, 308]) {
  for (const [kind, location, expected] of [
    [
      "root relative",
      "/workspace/usage?view=all#receipt",
      "/omega/api/workspace/usage?view=all#receipt",
    ],
    ["path relative", "usage?view=all", "/omega/api/workspace/usage?view=all"],
    [
      "same EE origin",
      ({ ee }) => `${ee}/workspace/usage?view=all`,
      "/omega/api/workspace/usage?view=all",
    ],
  ]) {
    test(`${status}: ${kind} Location is rewritten, never followed server-side`, async (t) => {
      const f = await fixture(t, { status, location });
      const response = await f.send();
      assert.equal(response.status, status);
      assert.equal(response.headers.get("location"), expected);
      assert.deepEqual(await response.json(), { upstream: true });
      assert.equal(f.arrivals.length, 1);
      assert.equal(f.secondArrivals.length, 0);
    });
  }
}

for (const [label, location] of [
  ["cross origin", ({ second }) => `${second}/capture`],
  [
    "network path to another origin",
    ({ second }) => `${second.slice("http:".length)}/capture`,
  ],
  ["invalid URL", "http://[broken"],
  ["non-HTTP scheme", "javascript:alert(1)"],
  [
    "credentials",
    ({ ee }) => ee.replace("http://", "http://user:pass@") + "/workspace/usage",
  ],
  ["backslash", ({ second }) => `/\\${second.slice("http://".length)}/capture`],
  ["invalid percent escape", "/workspace/%zz"],
  ["whitespace", "/workspace/bad path"],
  ["missing Location", undefined],
  ["empty Location", ""],
  [
    "incomplete absolute URL",
    ({ ee }) => ee.replace("http://", "http:/") + "/workspace/usage",
  ],
  [
    "extra slash in absolute URL",
    ({ ee }) => ee.replace("http://", "http:///") + "/workspace/usage",
  ],
  [
    "extra slash in network path",
    ({ ee }) => `///${ee.slice("http://".length)}/workspace/usage`,
  ],
  [
    "same-origin path that would become a network path",
    ({ ee }) => `${ee}//elsewhere/usage`,
  ],
]) {
  test(`bad redirect: ${label} returns the typed 502`, async (t) => {
    const f = await fixture(t, { location });
    const response = await f.send();
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error, "omega_remote_bad_redirect");
    assert.equal(response.headers.get("location"), null);
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(f.secondArrivals.length, 0);
  });
}

test("3xx uses the 200 response header filter, with only a validated Location added", async (t) => {
  const snapshots = [];
  for (const status of [200, 307, 308]) {
    const f = await fixture(t, { status, location: "/workspace/usage" });
    const response = await f.send();
    await response.text();
    assert.equal(response.status, status);
    snapshots.push(
      Object.fromEntries(
        [
          "set-cookie",
          "x-internal-route",
          "x-omega-authority",
          "etag",
          "retry-after",
          "x-omega-operation-id",
          "x-upstream-request-id",
          "cache-control",
          "content-type",
        ].map((name) => [name, response.headers.get(name)]),
      ),
    );
  }
  assert.equal(snapshots[0]["set-cookie"], null);
  assert.equal(snapshots[0]["x-internal-route"], null);
  assert.equal(snapshots[0]["x-omega-authority"], null);
  assert.equal(snapshots[0].etag, '"receipt"');
  assert.deepEqual(snapshots[1], snapshots[0]);
  assert.deepEqual(snapshots[2], snapshots[0]);
});

test("rewrite removes the configured EE mount and retains query and fragment", async (t) => {
  const f = await fixture(t, {
    remotePath: "/ee/api",
    location: ({ ee }) => `${ee}/ee/api/workspace/usage?view=all#receipt`,
  });
  const response = await f.send();
  assert.equal(response.status, 307);
  assert.equal(
    response.headers.get("location"),
    "/omega/api/workspace/usage?view=all#receipt",
  );
  await response.text();
  assert.equal(f.arrivals.length, 1);
});

for (const location of [
  "/outside/usage",
  "/ee/api-other/workspace/usage",
  "../../outside/usage",
]) {
  test(`a Location outside the EE mount has no public mapping: ${location}`, async (t) => {
    const f = await fixture(t, { remotePath: "/ee/api", location });
    const response = await f.send();
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error, "omega_remote_bad_redirect");
    assert.equal(f.arrivals.length, 1);
  });
}

for (const location of ["?view=all", "#receipt", ".", "../workspace/usage"]) {
  test(`relative reference is resolved against the original EE request: ${location}`, async (t) => {
    const f = await fixture(t, { location });
    const response = await f.send();
    assert.equal(response.status, 307);
    const resolved = new URL(location, `${f.ee}/workspace/deployments`);
    assert.equal(
      response.headers.get("location"),
      `/omega/api${resolved.pathname}${resolved.search}${resolved.hash}`,
    );
    await response.text();
    assert.equal(f.arrivals.length, 1);
  });
}

test("an unmounted gateway still rewrites to an origin-relative public path", async (t) => {
  const f = await fixture(t, {
    basePath: "",
    location: ({ ee }) => `${ee}/workspace/usage`,
  });
  const response = await f.send();
  assert.equal(response.status, 307);
  assert.equal(response.headers.get("location"), "/workspace/usage");
  await response.text();
});

test("304 without Location remains a conditional response, not a bad redirect", async (t) => {
  const f = await fixture(t, { status: 304 });
  const response = await f.send("/workspace/usage", {
    method: "GET",
    body: undefined,
  });
  assert.equal(response.status, 304);
  assert.equal(response.headers.get("location"), null);
  assert.equal(response.headers.get("set-cookie"), null);
  await response.text();
});

for (const status of [307, 308]) {
  test(`${status}: the credential exchange never follows redirects`, async (t) => {
    const f = await fixture(t, {
      status,
      location: ({ second }) => `${second}/exchange`,
    });
    const response = await f.send("/workspace/usage", {
      method: "GET",
      body: undefined,
      headers: { authorization: "Bearer fixture-browser-token" },
    });
    await response.text();
    assert.equal(f.secondArrivals.length, 0);
    assert.equal(f.arrivals[0].path, "/exchange");
    assert.equal(response.status, 401);
  });

  test(`${status}: readiness probes never follow redirects`, async (t) => {
    const f = await fixture(t, {
      status,
      location: ({ second }) => `${second}/probe`,
    });
    await f.gateway.lifecycle.readiness();
    assert.equal(f.secondArrivals.length, 0);
  });

  test(`${status}: OTLP export never follows redirects`, async (t) => {
    const f = await fixture(t, {
      status,
      location: ({ second }) => `${second}/traces`,
    });
    const exporter = createSpanExporter({
      url: `${f.ee}/traces`,
      credentials: null,
      projectName: "fixture",
      serviceName: "fixture",
    });
    t.after(() => exporter.shutdown());
    const context = startRequest({ method: "GET", headers: {} });
    context.sampled = true;
    exporter.record(context);
    await exporter.flush();
    assert.equal(f.secondArrivals.length, 0);
    assert.equal(exporter.stats.failedBatches, 1);
  });

  for (const [name, wrap] of [
    ["mint", learnMint],
    ["channel", followChannel],
  ]) {
    test(`${status}: ${name} fetch wrapper enforces manual redirects`, async (t) => {
      const f = await fixture(t, {
        status,
        location: ({ second }) => `${second}/channel`,
      });
      const response = await wrap(fetch, {})(`${f.ee}/channel`, {
        method: "POST",
        body: "fixture-channel-body",
        redirect: "follow",
      });
      await response.text();
      assert.equal(f.secondArrivals.length, 0);
      assert.equal(response.status, status);
    });
  }
}

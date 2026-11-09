// Readiness and drain (READINESS 1.3, S06, CH-02; CP REL2): `/readyz` reflects EE and the
// exchange, and SIGTERM drains within 10 s with open streams ended cleanly for reconnect.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createLifecycle } from "../src/lifecycle.mjs";
import { startChildGateway } from "./child.mjs";
import { cloudEnv, json, mintAssertion, startGateway } from "./helpers.mjs";

const readyBody = { status: "ready", ready: true };
const probeConfig = {
  remoteBaseUrl: "http://127.0.0.1:1",
  eeReadyPath: "/readyz",
  exchangeUrl: "http://127.0.0.1:1/session-exchange/",
};

function probeLifecycle(eeResponse, exchangeResponse = () => json({}, 401)) {
  return createLifecycle(probeConfig, {
    fetchImpl: async (url, init) => {
      assert.equal(init.method, "GET");
      assert.equal(init.redirect, "manual");
      assert.deepEqual(init.headers, { accept: "application/json" });
      return url === probeConfig.exchangeUrl
        ? exchangeResponse(init)
        : eeResponse(init);
    },
  });
}

for (const [name, response] of [
  ["204", () => new Response(null, { status: 204 })],
  [
    "200 text/html",
    () =>
      new Response("<html>default page</html>", {
        headers: { "content-type": "text/html" },
      }),
  ],
  ["200 JSON not-ready", () => json({ status: "not_ready", ready: false })],
  ["302", () => new Response(null, { status: 302 })],
  ["201 ready JSON", () => json(readyBody, 201)],
  ["missing readiness fields", () => json({})],
  ["string ready flag", () => json({ status: "ready", ready: "true" })],
  ["contradictory status", () => json({ status: "not_ready", ready: true })],
  [
    "malformed JSON",
    () =>
      new Response("{", { headers: { "content-type": "application/json" } }),
  ],
  ["JSON array", () => json([readyBody])],
  ["JSON null", () => json(null)],
  [
    "wrong JSON media type",
    () => json(readyBody, 200, { "content-type": "application/jsonp" }),
  ],
  [
    "oversized JSON",
    () => json({ ...readyBody, detail: "x".repeat(64 * 1024) }),
  ],
]) {
  test(`/readyz rejects EE ${name}`, async () => {
    assert.deepEqual(await probeLifecycle(response).readiness(), {
      ready: false,
      checks: { ee: "fail", exchange: "ok" },
    });
  });
}

for (const status of [200, 204, 302, 400, 403, 404, 405, 429, 500]) {
  test(`/readyz rejects exchange ${status}`, async () => {
    const lifecycle = probeLifecycle(
      () => json(readyBody),
      () => new Response(null, { status }),
    );
    assert.deepEqual(await lifecycle.readiness(), {
      ready: false,
      checks: { ee: "ok", exchange: "fail" },
    });
  });
}

test("/readyz accepts EE contract JSON and only the unauthenticated exchange refusal", async () => {
  const lifecycle = probeLifecycle(() =>
    json(readyBody, 200, { "content-type": "application/json; charset=utf-8" }),
  );
  assert.deepEqual(await lifecycle.readiness(), {
    ready: true,
    checks: { ee: "ok", exchange: "ok" },
  });
});

test("/readyz cancels an oversized EE stream without reading the remainder", async () => {
  let reads = 0;
  let cancelled = false;
  const lifecycle = probeLifecycle(
    () =>
      new Response(
        new ReadableStream(
          {
            pull(controller) {
              reads += 1;
              if (reads <= 5)
                controller.enqueue(new Uint8Array(16 * 1024).fill(32));
              else controller.close();
            },
            cancel() {
              cancelled = true;
            },
          },
          { highWaterMark: 0 },
        ),
        { headers: { "content-type": "application/json" } },
      ),
  );
  assert.deepEqual(await lifecycle.readiness(), {
    ready: false,
    checks: { ee: "fail", exchange: "ok" },
  });
  assert.equal(reads, 5);
  assert.equal(cancelled, true);
});

test("/readyz times out while reading EE's body", async () => {
  let aborted = false;
  const lifecycle = probeLifecycle(
    ({ signal }) =>
      new Response(
        new ReadableStream({
          start(controller) {
            signal.addEventListener(
              "abort",
              () => {
                aborted = true;
                controller.error(new Error("probe aborted"));
              },
              { once: true },
            );
          },
        }),
        { headers: { "content-type": "application/json" } },
      ),
  );
  assert.deepEqual(await lifecycle.readiness(), {
    ready: false,
    checks: { ee: "fail", exchange: "ok" },
  });
  assert.equal(aborted, true);
});

test("/readyz is ready only when EE is ready and the exchange answers; results are cached briefly", async (t) => {
  let ee = 200;
  let exchange = 401;
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    handler: {
      probe: (url) =>
        url.includes("session-exchange")
          ? json({}, exchange)
          : json(readyBody, ee),
    },
  });
  const ready = await fetch(`${base}/readyz`);
  assert.equal(ready.status, 200);
  assert.deepEqual((await ready.json()).checks, { ee: "ok", exchange: "ok" });
  assert.equal((await fetch(`${base}/readyz`)).status, 200);
  assert.equal(calls.probes.length, 2, "the second probe came from the cache");
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  ee = 503;
  await new Promise((resolve) => setTimeout(resolve, 2_100));
  const eeDown = await fetch(`${base}/readyz`);
  assert.equal(eeDown.status, 503);
  assert.deepEqual((await eeDown.json()).checks, {
    ee: "fail",
    exchange: "ok",
  });
  ee = 200;
  exchange = 502;
  await new Promise((resolve) => setTimeout(resolve, 2_100));
  const issuerDown = await fetch(`${base}/readyz`);
  assert.equal(issuerDown.status, 503);
  assert.deepEqual((await issuerDown.json()).checks, {
    ee: "ok",
    exchange: "fail",
  });
  // Liveness never depends on upstreams.
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
});

test("drain: streams end cleanly at once, in-flight work finishes, new work is refused, all within the window", async (t) => {
  let releaseSlow;
  const slowGate = new Promise((resolve) => {
    releaseSlow = resolve;
  });
  const { base, gateway } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    handler: {
      upstream: async (url) => {
        if (url.endsWith("/usage")) {
          await slowGate;
          return json({ finished: true });
        }
        return new Response(
          new ReadableStream({
            start(c) {
              c.enqueue(new TextEncoder().encode("id: 1\ndata: x\n\n"));
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    },
  });
  const streams = await Promise.all(
    Array.from({ length: 4 }, async () => {
      const response = await fetch(
        `${base}/workspace/conversations/c-1/events`,
        { headers: { authorization: `Bearer ${mintAssertion()}` } },
      );
      const reader = response.body.getReader();
      await reader.read();
      return reader;
    }),
  );
  const slow = fetch(`${base}/workspace/usage`, {
    headers: { authorization: `Bearer ${mintAssertion()}` },
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const started = performance.now();
  const draining = gateway.drain(10_000);
  // Every stream ends with a clean end (done), not an error: the client reconnects with Last-Event-ID.
  for (const reader of streams)
    assert.deepEqual(await reader.read(), { done: true, value: undefined });
  const streamsEndedMs = performance.now() - started;
  // New work is refused while draining: readiness is down, and a request is never served.
  assert.deepEqual(await gateway.lifecycle.readiness(), {
    ready: false,
    checks: { draining: true },
  });
  const late = await fetch(`${base}/workspace/capabilities`, {
    headers: { authorization: `Bearer ${mintAssertion()}` },
  }).then(
    async (r) => [r.status, (await r.json()).error],
    () => ["refused"],
  );
  assert.ok(
    late[0] === "refused" ||
      (late[0] === 503 && late[1] === "omega_gateway_draining"),
    String(late),
  );
  setTimeout(releaseSlow, 300);
  const finished = await slow;
  assert.equal(finished.status, 200);
  assert.deepEqual(await finished.json(), { finished: true });
  const outcome = await draining;
  assert.equal(outcome.streamsEnded, 4);
  assert.equal(outcome.abandoned, 0);
  assert.ok(outcome.drainMs < 10_000);
  t.diagnostic(
    `streams ended in ${streamsEndedMs.toFixed(0)} ms; drain finished in ${outcome.drainMs.toFixed(0)} ms`,
  );
  assert.ok(streamsEndedMs < 1_000);
});

test("drain: a request still running at the deadline is cut, and nothing waits longer than the window", async (t) => {
  const { base, gateway } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    handler: { upstream: () => new Promise(() => {}) },
  });
  const hung = fetch(`${base}/workspace/usage`, {
    headers: { authorization: `Bearer ${mintAssertion()}` },
  }).then(
    (r) => r.status,
    () => "cut",
  );
  await new Promise((resolve) => setTimeout(resolve, 100));
  const started = performance.now();
  const outcome = await gateway.drain(500);
  assert.ok(performance.now() - started < 1_500);
  assert.equal(outcome.abandoned, 1);
  assert.equal(await hung, "cut");
});

test("SIGTERM on the real process with 4 streams open: streams end, in-flight work finishes, exit within 10 s (CH-02)", async (t) => {
  const ee = http.createServer((request, response) => {
    if (request.url === "/exchange") return response.writeHead(401).end();
    if (request.url === "/readyz")
      return response
        .writeHead(200, { "content-type": "application/json" })
        .end(JSON.stringify(readyBody));
    if (request.url.endsWith("/usage")) {
      setTimeout(
        () =>
          response
            .writeHead(200, { "content-type": "application/json" })
            .end('{"slow":true}'),
        1_500,
      );
      return;
    }
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write("id: 1\ndata: x\n\n");
  });
  await new Promise((resolve) => ee.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        ee.closeAllConnections();
        ee.close(resolve);
      }),
  );
  const eeUrl = `http://127.0.0.1:${ee.address().port}`;
  const gateway = await startChildGateway(t, {
    OMEGA_REMOTE_BASE_URL: eeUrl,
    OMEGA_SESSION_EXCHANGE_URL: `${eeUrl}/exchange`,
  });
  const readers = await Promise.all(
    Array.from({ length: 4 }, async () => {
      const response = await fetch(
        `${gateway.base}/workspace/conversations/c-1/events`,
        { headers: { authorization: `Bearer ${mintAssertion()}` } },
      );
      const reader = response.body.getReader();
      await reader.read();
      return reader;
    }),
  );
  const slow = fetch(`${gateway.base}/workspace/usage`, {
    headers: { authorization: `Bearer ${mintAssertion()}` },
  });
  await new Promise((resolve) => setTimeout(resolve, 200));
  const sent = performance.now();
  gateway.child.kill("SIGTERM");
  for (const reader of readers) assert.equal((await reader.read()).done, true);
  const slowAnswer = await slow;
  assert.equal(
    slowAnswer.status,
    200,
    "in-flight work finished during the drain",
  );
  const exit = await gateway.exited;
  const exitMs = exit.at - sent;
  assert.equal(exit.code, 0);
  assert.ok(exitMs < 10_000, `exit after ${exitMs} ms`);
  const stopped = gateway.lines.find(
    (line) => line.event === "omega-gateway.stopped",
  );
  assert.equal(stopped.streamsEnded, 4);
  assert.equal(stopped.abandoned, 0);
  t.diagnostic(
    `SIGTERM to exit ${exitMs.toFixed(0)} ms with 4 streams and 1 slow read in flight`,
  );
});

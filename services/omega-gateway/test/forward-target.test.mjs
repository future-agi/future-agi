// What EE's socket receives is what wall W2 matched (READINESS L04; verifier finding 2026-10-17:
// a `#` in the target was matched raw, then cut by fetch, so EE saw another method and path).
// Raw request targets go through the real gateway with the real fetch to a loopback EE.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createGateway } from "../src/server.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { compileConsoleRouteManifest, MANIFEST_PATH } from "../src/manifest.mjs";
import { upstreamUrl } from "../src/upstream.mjs";
import { mintAssertion, routeProductsWith, writeJwks } from "./helpers.mjs";

const listen = (server) => new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

async function startPair(t, { manifest, routeProducts } = {}) {
  const received = [];
  const ee = http.createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      received.push(`${request.method} ${request.url}`);
      response.writeHead(200, { "content-type": "application/json" });
      response.end('{"ok":true}');
    });
  });
  await listen(ee);
  const config = loadConfig({
    OMEGA_GATEWAY_MODE: "local",
    OMEGA_REMOTE_BASE_URL: `http://127.0.0.1:${ee.address().port}`,
    OMEGA_SESSION_EXCHANGE_URL: "http://127.0.0.1:9/accounts/omega/session-exchange/",
    OMEGA_ASSERTION_JWKS_FILE: writeJwks(t),
    OMEGA_GATEWAY_BASE_PATH: "/omega/api",
  });
  const gateway = createGateway({ config, rateLimiter: createRateLimiter(), log: () => {}, fetchImpl: globalThis.fetch, ...(manifest ? { manifest } : {}), ...(routeProducts ? { routeProducts } : {}) });
  await listen(gateway.app);
  t.after(async () => {
    for (const server of [gateway.app, ee]) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
    gateway.rateLimiter.close();
    await gateway.exporter.shutdown?.();
  });
  return { port: gateway.app.address().port, received };
}

/** Send an exact request target (no client-side URL parsing). */
function rawRequest(port, method, path) {
  return new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port, method, path, headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json" } }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}") }));
    });
    request.on("error", reject);
    request.end(method === "GET" || method === "DELETE" ? undefined : "{}");
  });
}

test("every admitted target reaches EE's socket byte for byte as matched", async (t) => {
  const { port, received } = await startPair(t);
  const admitted = [
    ["GET", "/workspace/sandboxes/sbx-1/files/my%20file.txt"],
    ["GET", `/workspace/sandboxes/sbx-1/files/src/${encodeURIComponent("naïve plan (v2)!.md")}`],
    ["POST", "/workspace/conversations"],
    ["POST", "/workspace/conversations/c1/messages"],
    ["DELETE", "/workspace/conversations/c1"],
    ["POST", "/workspace/approvals/ap-1"],
    ["GET", "/workspace/conversations?limit=20&cursor=a%2Fb"],
  ];
  for (const [method, path] of admitted) {
    const answer = await rawRequest(port, method, `/omega/api${path}`);
    assert.equal(answer.status, 200, `${method} ${path}: ${JSON.stringify(answer.body)}`);
    assert.equal(received.at(-1), `${method} ${path}`);
  }
  assert.equal(received.length, admitted.length);
});

test("a target with `#` or a disguised literal is refused, and EE's socket receives nothing", async (t) => {
  const { port, received } = await startPair(t);
  for (const [method, path, status] of [
    // The verifier's four fragment targets.
    ["POST", "/omega/api/workspace/conversations/c1#/messages", 400],
    ["POST", "/omega/api/workspace/approvals/#x", 400],
    ["DELETE", "/omega/api/workspace/conversations/c1#", 400],
    ["GET", "/omega/api/workspace/sandboxes/s1/files/a#b", 400],
    // A rating write that would have arrived as a conversation start.
    ["POST", "/omega/api/workspace/conversations/#/rating", 400],
    ["GET", "/omega/api/workspace/usage?x=1#frag", 400],
    // %73ettings is `settings` once decoded; PATCH is not admitted on it.
    ["PATCH", "/omega/api/workspace/sandboxes/%73ettings", 403],
  ]) {
    const answer = await rawRequest(port, method, path);
    assert.equal(answer.status, status, `${method} ${path}`);
    assert.ok(["bad_request_target", "route_not_allowed"].includes(answer.body.error), `${method} ${path}: ${answer.body.error}`);
  }
  assert.deepEqual(received, []);
});

test("if a URL parser would change a matched path, the gateway sends nothing (the class, not just `#`)", async (t) => {
  // A test manifest whose parameter admits a raw `{`, which fetch re-encodes as %7B.
  const fs = await import("node:fs");
  const raw = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  const manifest = compileConsoleRouteManifest({
    ...raw,
    routes: [...raw.routes, { method: "GET", path: "/workspace/odd/{id}", role: "member", body: "none", timeout: "read", rate: "read" }],
  });
  // The test route needs a product too, or the product wall refuses it before the forward check.
  const routeProducts = await routeProductsWith([{ prefix: "/workspace/odd", product: "console" }]);
  const { port, received } = await startPair(t, { manifest, routeProducts });
  const answer = await rawRequest(port, "GET", "/omega/api/workspace/odd/a{b");
  assert.equal(answer.status, 400);
  assert.equal(answer.body.error, "bad_request_target");
  assert.deepEqual(received, []);
  assert.equal((await rawRequest(port, "GET", "/omega/api/workspace/odd/a%7Bb")).status, 200);
  assert.deepEqual(received, ["GET /workspace/odd/a%7Bb"]);
});

test("upstreamUrl keeps EE's base path and refuses any path the parser would rewrite", () => {
  assert.equal(upstreamUrl("https://ee.internal", "/workspace/x", "?a=1"), "https://ee.internal/workspace/x?a=1");
  assert.equal(upstreamUrl("https://ee.internal/api", "/workspace/x", ""), "https://ee.internal/api/workspace/x");
  assert.equal(upstreamUrl("https://ee.internal/api", "/workspace/my%20file", ""), "https://ee.internal/api/workspace/my%20file");
  for (const path of ["/workspace/a#b", "/workspace/a{b", "/workspace/a/../b", "/workspace/a/./b", "/workspace/a`b", "/workspace/a\"b", "/workspace/a<b"]) {
    assert.equal(upstreamUrl("https://ee.internal/api", path, ""), null, path);
  }
  assert.equal(upstreamUrl("https://ee.internal", "/workspace/x", "?a=1#frag"), null);
});

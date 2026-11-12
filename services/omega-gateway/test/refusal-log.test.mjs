// RC-03 at the gateway: every refusal the gateway answers itself writes exactly one JSON line with
// its reason (READINESS 3.13). The A2 verifier found the gateway logged nothing but its start line.
// A proxied answer (even EE's own 404), a preflight and a success write nothing, and no line ever
// holds a credential.
import test from "node:test";
import assert from "node:assert/strict";
import { createGatewayServer } from "../src/server.mjs";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { createRefusalLog } from "../src/refusal-log.mjs";
import { ALL_PRODUCTS_ACTIVE, claimsOf, foreignKey, json, mintAssertion, suiteJwksFile } from "./helpers.mjs";

const EXCHANGE = "https://futureagi.example.test/accounts/omega/session-exchange/";
const LOCAL_EXCHANGE = "http://127.0.0.1:9/accounts/omega/session-exchange/";
const JWKS = suiteJwksFile();

async function gateway(t, { jwks = JWKS, exchange, upstream } = {}) {
  const lines = [];
  // Cloud mode requires the JWKS (and a shared limit store, injected here as memory); a gateway
  // without a JWKS can only run in local mode, on loopback.
  const config = loadConfig(jwks
    ? { OMEGA_REMOTE_BASE_URL: "https://omega.example.test", OMEGA_SESSION_EXCHANGE_URL: EXCHANGE,
      OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379" }
    : { OMEGA_GATEWAY_MODE: "local", OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:9", OMEGA_SESSION_EXCHANGE_URL: LOCAL_EXCHANGE });
  const server = createGatewayServer({
    config,
    rateLimiter: createRateLimiter(),
    // `log` takes every request's access record; the refusal lines are their own sink (RC-03).
    log: () => {},
    refusalLog: createRefusalLog((line) => lines.push(JSON.parse(line)), () => "2026-10-18T00:00:00.000Z"),
    fetchImpl: async (url, init) => {
      if (url === config.exchangeUrl) return exchange ? exchange(init) : json({ accessToken: mintAssertion() });
      return upstream ? upstream(url, init) : json({ ok: true });
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { server.closeAllConnections(); server.close(() => resolve()); }));
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, path, authorization, headers = {}) => {
    const response = await fetch(`${base}${path}`, { method, headers: { ...(authorization ? { authorization } : {}), ...headers } });
    await response.arrayBuffer();
    return response.status;
  };
  return { call, lines };
}

test("each refusal writes one line with its status, error and reason", async (t) => {
  const { call, lines } = await gateway(t);
  const keyB = mintAssertion({ products: {}, org_id: "b-org", workspace_id: "b-ws" });
  const keyA = mintAssertion({ org_id: "a-org", workspace_id: "a-ws" });
  const pausedA = mintAssertion({ products: { ...ALL_PRODUCTS_ACTIVE, sandboxes: "paused" }, org_id: "a-org" });
  const [head, , signature] = keyB.split(".");
  const edited = `${head}.${Buffer.from(JSON.stringify({ ...claimsOf(keyB), products: ALL_PRODUCTS_ACTIVE })).toString("base64url")}.${signature}`;
  const cases = [
    [["GET", "/workspace/sandboxes", `Bearer ${keyB}`], { status: 404, error: "product_not_admitted", reason: "sandboxes", org_id: "b-org", workspace_id: "b-ws" }],
    [["GET", "/workspace/capabilities", `Bearer ${keyB}`], { status: 404, error: "product_not_admitted", reason: "platform", org_id: "b-org" }],
    [["POST", "/workspace/sandboxes", `Bearer ${pausedA}`], { status: 503, error: "product_paused", reason: "sandboxes", org_id: "a-org" }],
    [["GET", "/workspace/sandboxes", `Bearer ${edited}`], { status: 401, error: "omega_assertion_rejected", reason: "bad_signature" }],
    [["GET", "/workspace/sandboxes", `Bearer ${mintAssertion({}, { privateKey: foreignKey.privateKey })}`], { status: 401, error: "omega_assertion_rejected", reason: "bad_signature" }],
    [["GET", "/workspace/sandboxes", `Bearer ${keyA}`, { "x-organization-id": "b-org" }], { status: 403, error: "omega_scope_mismatch", org_id: "a-org" }],
    [["GET", "/WORKSPACE/sandboxes", `Bearer ${keyA}`], { status: 403, error: "route_not_allowed" }],
    [["HEAD", "/workspace/sandboxes", `Bearer ${keyA}`], { status: 403, error: "route_not_allowed" }],
    // The manifest's encoding rule (lane 1.3) refuses it before any credential is read: no ids.
    [["GET", "/workspace/%73andboxes", `Bearer ${keyA}`], { status: 403, error: "route_not_allowed", reason: "path_refused" }],
    [["GET", "/workspace/sandboxes?token=leaked-secret", `Bearer ${keyA}`], { status: 400, error: "credential_in_query" }],
    [["GET", "/workspace/sandboxes", undefined], { status: 401, error: "omega_session_unavailable" }],
    [["POST", "/healthz", undefined], { status: 405, error: "method_not_allowed" }],
  ];
  for (const [[method, path, authorization, headers], expected] of cases) {
    lines.length = 0;
    assert.equal(await call(method, path, authorization, headers), expected.status, `${method} ${path}`);
    assert.equal(lines.length, 1, `${method} ${path}: ${JSON.stringify(lines)}`);
    const [line] = lines;
    assert.equal(line.event, "omega_gateway_refused");
    assert.equal(line.method, method);
    assert.equal(line.path, path.split("?")[0]);
    for (const [field, value] of Object.entries(expected)) assert.equal(line[field], value, `${method} ${path} ${field}`);
    // Ids appear only from a key this gateway verified: never before the credential is checked, and
    // never from a key whose signature failed.
    if (!expected.org_id) assert.equal(line.org_id, undefined, `${method} ${path}: no unverified ids`);
    const raw = JSON.stringify(line);
    for (const secret of [keyA, keyB, pausedA, edited, "leaked-secret"]) assert.equal(raw.includes(secret), false);
  }
});

test("Django's refusals through the browser-token path are logged with the exchange as the reason", async (t) => {
  for (const [status, code] of [[404, "product_not_admitted"], [503, "verification_unavailable"]]) {
    const { call, lines } = await gateway(t, { exchange: () => json({ code }, status) });
    assert.equal(await call("GET", "/workspace/sandboxes", "Bearer browser-session-token"), status);
    assert.deepEqual(lines.map(({ status: s, error, reason }) => [s, error, reason]), [[status, code, "exchange"]]);
    assert.equal(JSON.stringify(lines).includes("browser-session-token"), false);
  }
});

test("a gateway with no JWKS logs its refusal but never the ids of a key it could not verify", async (t) => {
  const { call, lines } = await gateway(t, { jwks: null });
  assert.equal(await call("GET", "/workspace/sandboxes", `Bearer ${mintAssertion({ org_id: "claimed-org" })}`), 503);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].error, "verification_unavailable");
  assert.equal(lines[0].org_id, undefined);
});

test("proxied answers, preflights and successes write nothing; an unreachable EE is logged as a failure", async (t) => {
  const { call, lines } = await gateway(t, { upstream: (url) => (url.endsWith("/missing") ? json({ error: "not_found" }, 404) : json({ ok: true })) });
  assert.equal(await call("GET", "/workspace/sandboxes", `Bearer ${mintAssertion()}`), 200);
  assert.equal(await call("GET", "/workspace/sandboxes/missing", `Bearer ${mintAssertion()}`), 404);
  assert.equal(await call("OPTIONS", "/workspace/sandboxes", undefined), 204);
  assert.equal(await call("GET", "/healthz", undefined), 200);
  assert.deepEqual(lines, []);
  const down = await gateway(t, { upstream: () => { throw new Error("connect ECONNREFUSED"); } });
  assert.equal(await down.call("GET", "/workspace/sandboxes", `Bearer ${mintAssertion()}`), 502);
  assert.deepEqual(down.lines.map(({ event, status, error }) => [event, status, error]), [["omega_gateway_failed", 502, "omega_remote_unreachable"]]);
});

test("the line format: bounded fields, and a failing sink never changes the answer", () => {
  const lines = [];
  const log = createRefusalLog((line) => lines.push(JSON.parse(line)), () => "t");
  log({ method: "GET", path: `/workspace/${"x".repeat(2000)}`, status: 404, error: "product_not_admitted", reason: "r".repeat(500),
    claims: { org_id: "o", workspace_id: "w" }, verified: true });
  assert.equal(lines[0].path.length, 512);
  assert.equal(lines[0].reason.length, 128);
  assert.deepEqual([lines[0].org_id, lines[0].workspace_id], ["o", "w"]);
  const throwing = createRefusalLog(() => { throw new Error("disk full"); });
  assert.doesNotThrow(() => throwing({ method: "GET", path: "/", status: 404, error: "not_found" }));
});

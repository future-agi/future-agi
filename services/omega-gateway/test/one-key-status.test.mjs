// The Console's model connection status (GET /workspace/one-key) through the gateway: a member's read
// is admitted as a Console route, reaches EE, and EE's answer comes back exactly as EE gave it,
// status and body. The gateway never writes a status of its own for this read: whether the
// connection is ready, not set up or unavailable on a deployment is EE's answer to give.
import test from "node:test";
import assert from "node:assert/strict";
import { createGatewayServer } from "../src/server.mjs";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { json, mintAssertion, suiteJwksFile } from "./helpers.mjs";

const JWKS = suiteJwksFile();
const STATUS_PATH = "/workspace/one-key";
// EE's member answer when the key file holds no key (the gateway connection route, key "unavailable").
const NO_KEY_STATUS = Object.freeze({
  schema: "omega-ee/one-key-status/v1",
  mode: "hosted",
  moneyAuthority: "fa_wallet",
  role: "member",
  key: "unavailable",
});
// EE's answer from a hosted daemon that serves no model connection (its fixed one-key placeholder).
const EE_UNAVAILABLE = Object.freeze({
  schemaVersion: "omega-workspace/v1",
  error: {
    code: "one_key_unavailable",
    message: "One-key request refused.",
    detail: {
      oneKeyRefusal: {
        schema: "omega-ee/one-key-refusal/v1",
        code: "one_key_unavailable",
        httpStatus: 503,
        retryable: true,
        remedy: "retry_later",
        memberRemedy: "retry_later",
        phase: "before_inference",
        fundingFixes: false,
        reference: "one-key-console",
      },
    },
  },
});

const roomy = Object.fromEntries(
  ["read", "write", "model-start", "public-share"].map((name) => [
    name,
    [{ scope: "user", capacity: 1_000, perSecond: 0.001 }],
  ]),
);

async function gateway(t, eeAnswer) {
  const upstream = [];
  const refusals = [];
  const config = loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.test",
    OMEGA_SESSION_EXCHANGE_URL:
      "https://futureagi.example.test/accounts/omega/session-exchange/",
    OMEGA_ASSERTION_JWKS_FILE: JWKS,
    OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379",
  });
  const server = createGatewayServer({
    config,
    rateLimiter: createRateLimiter({ classes: roomy }),
    log: () => {},
    refusalLog: (line) => refusals.push(line),
    fetchImpl: async (url, init) => {
      upstream.push({ url, method: init.method });
      return eeAnswer();
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, path, claims = {}) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { authorization: `Bearer ${mintAssertion(claims)}` },
    });
    return {
      status: response.status,
      body: await response.json().catch(() => null),
    };
  };
  return { call, upstream, refusals };
}

test("a member's model connection status reaches EE and EE's status comes back unchanged", async (t) => {
  const { call, upstream, refusals } = await gateway(t, () =>
    json(NO_KEY_STATUS),
  );
  const answer = await call("GET", STATUS_PATH, { role: "member" });
  assert.equal(answer.status, 200);
  assert.deepEqual(answer.body, NO_KEY_STATUS);
  assert.deepEqual(
    upstream.map((entry) => `${entry.method} ${new URL(entry.url).pathname}`),
    [`GET ${STATUS_PATH}`],
  );
  assert.equal(refusals.length, 0);
});

test("EE's answer from a deployment without a model connection passes back as EE gave it, status and body", async (t) => {
  const { call, upstream, refusals } = await gateway(t, () =>
    json(EE_UNAVAILABLE, 503),
  );
  const answer = await call("GET", STATUS_PATH, { role: "member" });
  assert.equal(answer.status, 503);
  assert.deepEqual(answer.body, EE_UNAVAILABLE);
  assert.equal(upstream.length, 1);
  assert.equal(refusals.length, 0, "the gateway gave no answer of its own");
});

test("the status read belongs to the Console product: a workspace without the Console is not admitted", async (t) => {
  const { call, upstream } = await gateway(t, () => json(NO_KEY_STATUS));
  const dark = await call("GET", STATUS_PATH, {
    products: { sandboxes: "active" },
  });
  assert.equal(dark.status, 404);
  assert.equal(dark.body.error, "product_not_admitted");
  const paused = await call("GET", STATUS_PATH, {
    products: { console: "paused" },
  });
  assert.equal(paused.status, 200, "a paused Console still reads its status");
  assert.equal(upstream.length, 1);
});

test("no other method reaches the status path, and the key intake and recovery stay closed", async (t) => {
  const { call, upstream } = await gateway(t, () => json(NO_KEY_STATUS));
  for (const [method, path] of [
    ["POST", STATUS_PATH],
    ["PUT", STATUS_PATH],
    ["DELETE", STATUS_PATH],
    ["POST", "/workspace/one-key/public-key"],
    ["POST", "/workspace/one-key/recovery"],
  ]) {
    const answer = await call(method, path, { role: "owner" });
    assert.equal(answer.status, 403, `${method} ${path}`);
    assert.equal(answer.body.error, "route_not_allowed", `${method} ${path}`);
  }
  assert.equal(upstream.length, 0);
});

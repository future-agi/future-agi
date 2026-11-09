// READINESS L03 (lane 3.7, SB W2-KEYS amended): 30 sandbox creates a minute per workspace, at
// the gateway. The exec caps (8 per sandbox, 32 per workspace) are EE's (EE-TENANT-LIMITS); the
// gateway forwards their 429 and Retry-After unchanged, which the last test pins.
import test from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/policy.mjs";
import { checkRouteRateClasses, rateClassOf, ROUTE_RATE_CLASSES } from "../src/rate-class.mjs";
import { bucketsFor, createMemoryStore, createRateLimiter, FALLBACK_CLASSES, RATE_CLASSES, STORE_OUTAGE_RETRY_SECONDS } from "../src/ratelimit.mjs";
import { ROUTE_MANIFEST } from "../src/routes.mjs";
import { cloudEnv, json, mintAssertion, mintKeyAssertion, startGateway } from "./helpers.mjs";

const A = { sub: "user-a", orgId: "org-a", workspaceId: "ws-a", ip: "198.51.100.7" };
const B = { sub: "user-b", orgId: "org-b", workspaceId: "ws-b", ip: "198.51.100.8" };

test("the committed number: a sandbox create is a write AND one of 30 a minute per workspace", () => {
  const [user, workspace] = RATE_CLASSES["sandbox-create"];
  // The user bucket IS the write bucket: same numbers, same key.
  assert.deepEqual({ ...user, bucket: undefined }, { ...RATE_CLASSES.write[0], bucket: undefined });
  assert.equal(bucketsFor("sandbox-create", A)[0].key, bucketsFor("write", A)[0].key);
  assert.deepEqual([workspace.scope, workspace.capacity, workspace.perSecond * 60], ["workspace", 30, 30]);
  // Every bucket of one request shares one Redis Cluster slot.
  const tags = bucketsFor("sandbox-create", A).map((bucket) => /\{([^}]+)\}/.exec(bucket.key)[1]);
  assert.equal(tags[0], tags[1]);
  // OD-07: like every write, a create is refused, never admitted per replica, while Redis is down.
  assert.equal(FALLBACK_CLASSES.includes("sandbox-create"), false);
});

test("only POST /workspace/sandboxes is refined, and the table is checked against the manifest", () => {
  assert.deepEqual({ ...ROUTE_RATE_CLASSES }, { "POST /workspace/sandboxes": "sandbox-create" });
  const route = (method, path) => ROUTE_MANIFEST.match(method, path).route;
  assert.equal(rateClassOf(route("POST", "/workspace/sandboxes")), "sandbox-create");
  for (const [method, path, rateClass] of [
    ["GET", "/workspace/sandboxes", "read"],
    ["POST", "/workspace/sandboxes/sb-1/exec", "write"],
    ["DELETE", "/workspace/sandboxes/sb-1", "write"],
    ["POST", "/workspace/conversations", "model-start"],
  ]) assert.equal(rateClassOf(route(method, path)), rateClass, `${method} ${path}`);
  assert.throws(() => checkRouteRateClasses(ROUTE_MANIFEST, { "POST /workspace/sandboxez": "sandbox-create" }), /names no manifest route/);
  assert.throws(() => checkRouteRateClasses(ROUTE_MANIFEST, { "GET /workspace/sandboxes": "sandbox-create" }), /must refine a write route/);
  assert.throws(() => checkRouteRateClasses(ROUTE_MANIFEST, { "POST /workspace/sandboxes": "sandbox-crate" }), /unknown class/);
});

test("the workspace's 30 are shared by its users, a refusal spends no write token, and another workspace is untouched", async () => {
  const store = createMemoryStore();
  const t0 = 9_000_000;
  // Four users of one workspace, under their own write bursts of 10, create 30 between them.
  for (let n = 0; n < 30; n++) {
    assert.equal((await store.take(bucketsFor("sandbox-create", { ...A, sub: `u${n % 4}` }), t0)).allowed, true, `create ${n + 1}`);
  }
  const fifth = { ...A, sub: "u4" };
  const refused = await store.take(bucketsFor("sandbox-create", fifth), t0);
  // One workspace token every 2 s.
  assert.deepEqual(refused, { allowed: false, retryAfterMs: 2_000 });
  // The refused create cost the fifth user nothing: all ten of its writes still run.
  for (let i = 0; i < 10; i++) assert.equal((await store.take(bucketsFor("write", fifth), t0)).allowed, true, `write ${i}`);
  assert.equal((await store.take(bucketsFor("write", fifth), t0)).allowed, false);
  // After 2 s one more create runs (the fifth user's write bucket has refilled too).
  assert.equal((await store.take(bucketsFor("sandbox-create", fifth), t0 + 1_999)).allowed, false);
  assert.equal((await store.take(bucketsFor("sandbox-create", fifth), t0 + 2_000)).allowed, true);
  assert.equal((await store.take(bucketsFor("sandbox-create", B), t0)).allowed, true);
});

test("a create also spends the user's write burst, so creates never add a second burst of writes", async () => {
  const store = createMemoryStore();
  const t0 = 12_000_000;
  for (let i = 0; i < 10; i++) assert.equal((await store.take(bucketsFor("write", A), t0)).allowed, true);
  const create = await store.take(bucketsFor("sandbox-create", A), t0);
  assert.deepEqual(create, { allowed: false, retryAfterMs: 200 });
});

test("OD-07: with the shared store down a create is refused, never admitted per replica", async () => {
  const broken = { kind: "redis", async take() { throw new Error("redis connection closed"); }, close() {} };
  const fallback = createMemoryStore();
  const limiter = createRateLimiter({ store: broken, fallback });
  assert.deepEqual(await limiter.take("sandbox-create", A), { allowed: false, unavailable: true, retryAfterSeconds: STORE_OUTAGE_RETRY_SECONDS, store: "unavailable" });
  assert.equal(fallback.size(), 0);
});

const configFor = (t) => loadConfig(cloudEnv(t));
const WS = { org_id: "22222222-2222-4222-8222-222222222222", workspace_id: "33333333-3333-4333-8333-333333333333" };
const OTHER_WS = { org_id: "66666666-6666-4666-8666-666666666666", workspace_id: "77777777-7777-4777-8777-777777777777" };
const userSub = (n) => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;

test("through the server: the 31st create of a workspace in a burst is 429 sandbox-create with Retry-After, EE never sees it, and the workspace recovers", async (t) => {
  // The real classes; only the workspace refill is slowed, so the arithmetic does not depend on
  // how loaded the host is (a refill of 30 a minute adds a token every 2 s).
  const classes = { ...RATE_CLASSES, "sandbox-create": [RATE_CLASSES["sandbox-create"][0], { scope: "workspace", capacity: 30, perSecond: 0.001 }] };
  const { base, calls } = await startGateway(t, { config: configFor(t), rateLimiter: createRateLimiter({ classes }) });
  const create = (claims) => fetch(`${base}/workspace/sandboxes`, {
    method: "POST", headers: { authorization: `Bearer ${mintAssertion(claims)}`, "content-type": "application/json" }, body: "{}",
  });
  const statuses = [];
  for (let n = 0; n < 30; n++) statuses.push((await create({ ...WS, sub: userSub(n % 4) })).status);
  assert.deepEqual(statuses, Array(30).fill(200));
  const over = await create({ ...WS, sub: userSub(9) });
  assert.equal(over.status, 429);
  assert.ok(Number(over.headers.get("retry-after")) >= 1);
  const body = await over.json();
  assert.equal(body.error, "omega_rate_limited");
  assert.equal(body.reason, "rate_limited");
  assert.equal(body.rateClass, "sandbox-create");
  assert.equal(body.retryAfterSeconds, Number(over.headers.get("retry-after")));
  assert.equal(calls.upstream.length, 30, "EE received a create over the workspace limit");
  // Other sandbox work in the same workspace is not a create and still runs.
  const exec = await fetch(`${base}/workspace/sandboxes/sb-1/exec`, {
    method: "POST", headers: { authorization: `Bearer ${mintAssertion({ ...WS, sub: userSub(9) })}`, "content-type": "application/json" }, body: "{}",
  });
  assert.equal(exec.status, 200);
  assert.equal((await fetch(`${base}/workspace/sandboxes`, { headers: { authorization: `Bearer ${mintAssertion({ ...WS, sub: userSub(9) })}` } })).status, 200);
  // Another workspace creates at once.
  assert.equal((await create({ ...OTHER_WS, sub: userSub(1) })).status, 200);
});

test("through the server, at the real refill: a create refused for the workspace runs again after its Retry-After", async (t) => {
  const { base } = await startGateway(t, { config: configFor(t) });
  const create = (n) => fetch(`${base}/workspace/sandboxes`, {
    method: "POST", headers: { authorization: `Bearer ${mintAssertion({ ...WS, sub: userSub(n % 5) })}`, "content-type": "application/json" }, body: "{}",
  });
  // Concurrent, so the burst lands well inside one refill period: at most one token (2 s) can refill meanwhile.
  const started = performance.now();
  const burst = await Promise.all(Array.from({ length: 40 }, (_, n) => create(n)));
  const elapsedS = (performance.now() - started) / 1000;
  const allowed = burst.filter((response) => response.status === 200).length;
  assert.ok(allowed >= 30 && allowed <= 30 + Math.ceil(elapsedS / 2), `allowed ${allowed} of 40 in ${elapsedS.toFixed(3)} s`);
  const refused = burst.find((response) => response.status === 429);
  assert.ok(refused, "no 429 in a burst of 40 creates");
  const wait = Number(refused.headers.get("retry-after"));
  assert.ok(wait >= 1 && wait <= 2, `Retry-After ${wait}`);
  assert.equal((await refused.json()).rateClass, "sandbox-create");
  await Promise.all(burst.map((response) => response.body?.cancel().catch(() => {})));
  await new Promise((resolve) => setTimeout(resolve, wait * 1000 + 50));
  assert.equal((await create(41)).status, 200);
});

test("EE's exec caps (8 per sandbox, 32 per workspace) reach an API-key caller unchanged, with Retry-After", async (t) => {
  const detail = { retryAfterSeconds: 1, limit: "exec_per_workspace", scope: "workspace", cap: 32 };
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: () => json({ accessToken: mintKeyAssertion() }),
      upstream: () => json({ error: { code: "rate_limited", message: "This workspace already runs 32 commands at once.", detail } }, 429, { "retry-after": "1" }),
    },
  });
  const response = await fetch(`${base}/workspace/sandboxes/sb-1/exec`, {
    method: "POST", headers: { "x-api-key": "pub-1", "x-secret-key": "secret-1", "content-type": "application/json" }, body: "{}",
  });
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "1");
  assert.deepEqual((await response.json()).error.detail, detail);
  assert.equal(calls.upstream.length, 1);
});

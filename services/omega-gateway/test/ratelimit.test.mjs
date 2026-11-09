// Rate limits (CP P5, SEC7; READINESS L03, OD-07, CH-08): bucket arithmetic, keys, the Redis
// outage policy, and the 429
// the browser sees, per class, through the real server.
import test from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/policy.mjs";
import { bucketsFor, createMemoryStore, createRateLimiter, FALLBACK_CLASSES, RATE_CLASSES, STORE_OUTAGE_RETRY_SECONDS } from "../src/ratelimit.mjs";
import { cloudEnv, mintAssertion, routeProductsWith, startGateway } from "./helpers.mjs";

const A = { sub: "user-a", orgId: "org-a", workspaceId: "ws-a", ip: "198.51.100.7" };
const A2 = { ...A, sub: "user-a2" };
const B = { sub: "user-b", orgId: "org-b", workspaceId: "ws-b", ip: "198.51.100.8" };

test("the committed numbers: read 30/s burst 60, write 5/s burst 10, model-start 20/min user and 120/min workspace, share 60/min per IP", () => {
  assert.deepEqual(RATE_CLASSES.read, [{ scope: "user", capacity: 60, perSecond: 30 }]);
  assert.deepEqual(RATE_CLASSES.write, [{ scope: "user", capacity: 10, perSecond: 5 }]);
  assert.deepEqual(RATE_CLASSES["model-start"].map((spec) => [spec.scope, spec.capacity, spec.perSecond * 60]), [["user", 20, 20], ["workspace", 120, 120]]);
  assert.deepEqual(RATE_CLASSES["public-share"].map((spec) => [spec.scope, spec.capacity, spec.perSecond * 60]), [["ip", 60, 60]]);
  // CX-WALL: a terminal sends about one request per keystroke; 5/s would cut a person's shell.
  assert.deepEqual(RATE_CLASSES.channel, [{ scope: "user", capacity: 120, perSecond: 40 }]);
});

test("bucket keys carry hashes, never raw ids, and one hash tag per request", () => {
  const keys = bucketsFor("model-start", A).map((bucket) => bucket.key);
  assert.equal(keys.length, 2);
  for (const key of keys) {
    for (const value of Object.values(A)) assert.equal(key.includes(value), false, `${key} leaks ${value}`);
  }
  const tags = keys.map((key) => /\{([^}]+)\}/.exec(key)[1]);
  assert.equal(tags[0], tags[1]);
  assert.notEqual(bucketsFor("read", A)[0].key, bucketsFor("read", A2)[0].key);
  assert.notEqual(bucketsFor("read", A)[0].key, bucketsFor("read", B)[0].key);
  assert.throws(() => bucketsFor("unknown", A), /Unknown rate class/);
});

test("a burst spends the bucket, the next request waits for one token, and the bucket refills", async () => {
  const store = createMemoryStore();
  const buckets = bucketsFor("write", A);
  const t0 = 1_000_000;
  for (let i = 0; i < 10; i++) assert.equal((await store.take(buckets, t0)).allowed, true, `burst ${i}`);
  const refused = await store.take(buckets, t0);
  assert.deepEqual(refused, { allowed: false, retryAfterMs: 200 });
  assert.equal((await store.take(buckets, t0 + 199)).allowed, false);
  assert.equal((await store.take(buckets, t0 + 200)).allowed, true);
  // Two seconds of refill is 10 tokens, never more than the burst.
  for (let i = 0; i < 10; i++) assert.equal((await store.take(buckets, t0 + 10_000)).allowed, true);
  assert.equal((await store.take(buckets, t0 + 10_000)).allowed, false);
});

test("model-start takes from both buckets or neither, so a workspace refusal costs the user nothing", async () => {
  const store = createMemoryStore();
  const t0 = 5_000_000;
  // Six users of one workspace spend its 120 per minute.
  for (let user = 0; user < 6; user++) {
    for (let i = 0; i < 20; i++) assert.equal((await store.take(bucketsFor("model-start", { ...A, sub: `u${user}` }), t0)).allowed, true);
  }
  const seventh = bucketsFor("model-start", { ...A, sub: "u6" });
  const refused = await store.take(seventh, t0);
  assert.equal(refused.allowed, false);
  assert.equal(refused.retryAfterMs, 500);
  // The seventh user's own bucket is still full: after one workspace token refills, it runs.
  assert.equal((await store.take(seventh, t0 + 500)).allowed, true);
  // Another workspace is untouched.
  assert.equal((await store.take(bucketsFor("model-start", B), t0)).allowed, true);
});

test("the memory store stays bounded", async () => {
  const store = createMemoryStore({ maxBuckets: 100 });
  for (let i = 0; i < 1_000; i++) await store.take(bucketsFor("read", { ...A, sub: `u${i}` }), 1_000);
  assert.ok(store.size() <= 101, `size ${store.size()}`);
});

test("OD-07: when the shared store fails, writes and model starts are refused and reads fall back, each counted and logged once", async () => {
  const logs = [];
  const broken = { kind: "redis", async take() { throw new Error("redis connection closed"); }, close() {} };
  const fallback = createMemoryStore();
  // The read refill is slowed so the burst arithmetic does not depend on how loaded the host is.
  const classes = { ...RATE_CLASSES, read: [{ scope: "user", capacity: 60, perSecond: 0.001 }] };
  const limiter = createRateLimiter({ store: broken, fallback, classes, log: (event) => logs.push(event) });
  // Writes and model starts fail closed: no request is admitted and no token is spent anywhere.
  for (const rateClass of ["write", "model-start"]) {
    for (let i = 0; i < 25; i++) {
      assert.deepEqual(await limiter.take(rateClass, A), { allowed: false, unavailable: true, retryAfterSeconds: STORE_OUTAGE_RETRY_SECONDS, store: "unavailable" }, `${rateClass} #${i}`);
    }
  }
  assert.equal(fallback.size(), 0);
  // Reads fall back to this replica's buckets, with their usual burst.
  for (let i = 0; i < 60; i++) assert.equal((await limiter.take("read", A)).allowed, true, `read #${i}`);
  const over = await limiter.take("read", A);
  assert.deepEqual([over.allowed, over.store], [false, "memory-fallback"]);
  assert.deepEqual(limiter.stats, { allowed: 60, refused: 1, fallbacks: 61, unavailable: 50 });
  assert.deepEqual(logs.map((event) => event.event), ["omega-gateway.ratelimit.store-unavailable", "omega-gateway.ratelimit.fallback"]);
  assert.deepEqual([...FALLBACK_CLASSES], ["read", "public-share", "channel"]);
});

test("OD-07 fails closed by default: a class not named as a fallback class is refused when the store fails", async () => {
  const broken = { kind: "redis", async take() { throw new Error("redis command timed out"); }, close() {} };
  const limiter = createRateLimiter({ store: broken, classes: { ...RATE_CLASSES, export: [{ scope: "user", capacity: 5, perSecond: 1 }] } });
  assert.equal((await limiter.take("export", A)).unavailable, true);
  assert.equal((await limiter.take("public-share", A)).store, "memory-fallback");
});

test("a healthy shared store decides every class itself, and is warmed at start", async () => {
  let warmed = 0;
  const shared = createMemoryStore();
  const store = { kind: "redis", take: (buckets) => shared.take(buckets), warm() { warmed += 1; }, close() {} };
  const limiter = createRateLimiter({ store });
  assert.equal(warmed, 1);
  for (const rateClass of ["read", "write", "model-start"]) assert.deepEqual(await limiter.take(rateClass, A), { allowed: true, retryAfterSeconds: 0, store: "redis" });
});

const configFor = (t) => loadConfig(cloudEnv(t));
const read = (base, token) => fetch(`${base}/workspace/capabilities`, { headers: { authorization: `Bearer ${token}` } });

test("over the read limit the browser gets 429 with Retry-After, and EE never sees the excess", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  // 80 at once: the burst of 60 plus whatever refilled at 30/s while they were in flight.
  const started = performance.now();
  const responses = await Promise.all(Array.from({ length: 80 }, () => read(base, mintAssertion())));
  const elapsedS = (performance.now() - started) / 1000;
  const allowed = responses.filter((response) => response.status === 200).length;
  assert.ok(allowed >= 60 && allowed <= 60 + Math.ceil(elapsedS * 30) + 1, `allowed ${allowed} in ${elapsedS.toFixed(3)} s`);
  const refused = responses.find((response) => response.status === 429);
  assert.ok(refused, "no 429 in a burst of 80");
  assert.ok(Number(refused.headers.get("retry-after")) >= 1);
  const body = await refused.json();
  assert.equal(body.error, "omega_rate_limited");
  assert.equal(body.rateClass, "read");
  assert.equal(body.retryAfterSeconds, Number(refused.headers.get("retry-after")));
  assert.equal(body.requestId, refused.headers.get("x-request-id"));
  assert.equal(calls.upstream.length, allowed);
  await Promise.all(responses.map((response) => response.body?.cancel().catch(() => {})));
});

test("each class is limited by its own numbers, and recovers after its window", async (t) => {
  const small = {
    read: [{ scope: "user", capacity: 3, perSecond: 20 }],
    write: [{ scope: "user", capacity: 2, perSecond: 20 }],
    "model-start": [{ scope: "user", capacity: 2, perSecond: 20 }, { scope: "workspace", capacity: 3, perSecond: 20 }],
    "public-share": [{ scope: "ip", capacity: 1, perSecond: 1 }],
  };
  const { base } = await startGateway(t, { config: configFor(t), rateLimiter: createRateLimiter({ classes: small }) });
  const send = (method, path, claims = {}) => fetch(`${base}${path}`, {
    method, headers: { authorization: `Bearer ${mintAssertion(claims)}`, "content-type": "application/json" }, body: method === "GET" ? undefined : "{}",
  });
  // The write sample is a visible member route: agent teams are hidden (manifest `hidden`, HIDE-API) and answer 404.
  for (const [method, path, burst] of [["GET", "/workspace/usage", 3], ["POST", "/workspace/workforce/agents", 2], ["POST", "/workspace/conversations", 2]]) {
    for (let i = 0; i < burst; i++) assert.equal((await send(method, path)).status, 200, `${method} ${path} #${i}`);
    const over = await send(method, path);
    assert.equal(over.status, 429, `${method} ${path}`);
    assert.equal(over.headers.get("retry-after"), "1");
  }
  // The workspace bucket: a second user of the same workspace gets one more model start, then 429.
  const other = { sub: "44444444-4444-4444-8444-444444444444" };
  assert.equal((await send("POST", "/workspace/conversations", other)).status, 200);
  assert.equal((await send("POST", "/workspace/conversations", other)).status, 429);
  await new Promise((resolve) => setTimeout(resolve, 250));
  assert.equal((await send("GET", "/workspace/usage")).status, 200);
  assert.equal((await send("POST", "/workspace/conversations")).status, 200);
});

test("a refused route, role or credential spends no token", async (t) => {
  const limiter = createRateLimiter({ classes: { ...RATE_CLASSES, "model-start": [{ scope: "user", capacity: 1, perSecond: 0.001 }] } });
  const { base } = await startGateway(t, { config: configFor(t), rateLimiter: limiter });
  // A member below the admin floor of agent drafts is refused before the limiter.
  const member = await fetch(`${base}/workspace/workforce/agent-drafts`, { method: "POST", headers: { authorization: `Bearer ${mintAssertion()}` }, body: "{}" });
  assert.equal(member.status, 403);
  assert.equal((await member.json()).error, "omega_role_not_permitted");
  assert.equal(limiter.stats.allowed + limiter.stats.refused, 0);
  const admin = await fetch(`${base}/workspace/workforce/agent-drafts`, { method: "POST", headers: { authorization: `Bearer ${mintAssertion({ role: "admin" })}` }, body: "{}" });
  assert.equal(admin.status, 200);
});

test("OD-07 through the server: with the shared store down, writes and model starts are 503 with Retry-After and never reach EE; reads still answer", async (t) => {
  const broken = { kind: "redis", async take() { throw new Error("redis connection closed"); }, close() {} };
  const { base, calls } = await startGateway(t, { config: configFor(t), rateLimiter: createRateLimiter({ store: broken }) });
  const send = (method, path) => fetch(`${base}${path}`, {
    method, headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json" }, body: method === "GET" ? undefined : "{}",
  });
  for (const [method, path, rateClass, n] of [["POST", "/workspace/agent-teams", "write", 15], ["POST", "/workspace/conversations", "model-start", 25]]) {
    for (let i = 0; i < n; i++) {
      const response = await send(method, path);
      assert.equal(response.status, 503, `${rateClass} #${i}`);
      assert.equal(response.headers.get("retry-after"), String(STORE_OUTAGE_RETRY_SECONDS));
      const body = await response.json();
      assert.equal(body.error, "omega_rate_limit_unavailable");
      assert.equal(body.reason, "rate_store_unavailable");
      assert.equal(body.rateClass, rateClass);
      assert.equal(body.retryAfterSeconds, STORE_OUTAGE_RETRY_SECONDS);
      assert.equal(body.requestId, response.headers.get("x-request-id"));
    }
  }
  assert.equal(calls.upstream.length, 0, "a write or model start reached EE while the limit store was down");
  for (let i = 0; i < 5; i++) assert.equal((await send("GET", "/workspace/usage")).status, 200);
  assert.equal(calls.upstream.length, 5);
});

test("noisy neighbour: while tenant A is throttled, tenant B is never refused", async (t) => {
  const { base } = await startGateway(t, { config: configFor(t) });
  const bClaims = { sub: "55555555-5555-4555-8555-555555555555", org_id: "66666666-6666-4666-8666-666666666666", workspace_id: "77777777-7777-4777-8777-777777777777" };
  const timeB = async (n) => {
    const samples = [];
    for (let i = 0; i < n; i++) {
      const started = performance.now();
      const response = await read(base, mintAssertion(bClaims));
      await response.arrayBuffer();
      samples.push(performance.now() - started);
      assert.equal(response.status, 200, `B refused at ${i}`);
      // B stays under its own 30/s.
      if (i % 25 === 24) await new Promise((resolve) => setTimeout(resolve, 900));
    }
    return samples.sort((x, y) => x - y);
  };
  const baseline = await timeB(100);
  let refusedA = 0;
  let floodOn = true;
  const flood = (async () => {
    while (floodOn) {
      const batch = await Promise.all(Array.from({ length: 20 }, () => read(base, mintAssertion()).then(async (r) => { await r.arrayBuffer(); return r.status; })));
      refusedA += batch.filter((status) => status === 429).length;
    }
  })();
  const during = await timeB(100);
  floodOn = false;
  await flood;
  assert.ok(refusedA > 0, "A was never throttled");
  const p50 = (samples) => samples[Math.floor(samples.length / 2)];
  t.diagnostic(`B p50 ${p50(baseline).toFixed(2)} ms alone, ${p50(during).toFixed(2)} ms while A was refused ${refusedA} times (n=100 each; component check, not the rig's p95)`);
});

test("the public share class is closed by default, and per IP when opened (until CP wave 3)", async (t) => {
  const { compileConsoleRouteManifest, MANIFEST_PATH } = await import("../src/manifest.mjs");
  const fs = await import("node:fs");
  const raw = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  // Today's manifest has no public-share class at all, so no such route can even be declared.
  assert.equal(raw.classes.rate.includes("public-share"), false);
  const withShare = compileConsoleRouteManifest({
    ...raw,
    classes: { ...raw.classes, rate: [...raw.classes.rate, "public-share"] },
    routes: [...raw.routes, { method: "GET", path: "/workspace/replay/shared/{id}", role: "member", body: "none", timeout: "read", rate: "public-share" }],
  });
  // A shared replay is a Console conversation; the test route needs its product, as every route does.
  const routeProducts = await routeProductsWith([{ prefix: "/workspace/replay", product: "console" }]);
  const closed = await startGateway(t, { config: configFor(t), manifest: withShare, routeProducts });
  const refused = await fetch(`${closed.base}/workspace/replay/shared/tok-1`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(refused.status, 404);
  assert.equal((await refused.json()).reason, "public_share_closed");
  assert.equal(closed.calls.upstream.length, 0);
  const open = await startGateway(t, {
    config: loadConfig(cloudEnv(t, { OMEGA_GATEWAY_PUBLIC_SHARE: "on" })), manifest: withShare, routeProducts,
    rateLimiter: createRateLimiter({ classes: { ...RATE_CLASSES, "public-share": [{ scope: "ip", capacity: 2, perSecond: 1 / 60 }] } }),
  });
  const statuses = [];
  // Different users from one IP share the IP's bucket.
  for (const sub of ["11111111-1111-4111-8111-111111111111", "22222222-1111-4111-8111-111111111111", "33333333-1111-4111-8111-111111111111"]) {
    statuses.push((await fetch(`${open.base}/workspace/replay/shared/tok-1`, { headers: { authorization: `Bearer ${mintAssertion({ sub })}` } })).status);
  }
  assert.deepEqual(statuses, [200, 200, 429]);
});

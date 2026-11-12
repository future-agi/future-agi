// Shared buckets in Redis (CP P5, REL4): the RESP client, the Lua bucket, and limits that hold
// across two gateway replicas. The live part needs a real Redis:
//   OMEGA_GATEWAY_TEST_REDIS_URL=redis://127.0.0.1:16379 node --test test/ratelimit-redis.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { loadConfig } from "../src/policy.mjs";
import { bucketsFor, createMemoryStore, createRateLimiter } from "../src/ratelimit.mjs";
import { createRedisClient, createRedisStore, encodeCommand, parseReply, RedisReplyError } from "../src/ratelimit-redis.mjs";
import { defaultRateLimiter } from "../src/server.mjs";
import { cloudEnv, mintAssertion, startGateway } from "./helpers.mjs";

const REDIS_URL = process.env.OMEGA_GATEWAY_TEST_REDIS_URL;
const live = REDIS_URL ? {} : { skip: "set OMEGA_GATEWAY_TEST_REDIS_URL to run against a real Redis" };
function redisOptions() {
  const url = new URL(REDIS_URL);
  return { host: url.hostname, port: Number(url.port || 6379), db: 9 };
}

test("RESP commands are encoded as bulk strings and every reply type parses, across chunks", () => {
  assert.equal(encodeCommand(["EVAL", "return 1", 0]).toString(), "*3\r\n$4\r\nEVAL\r\n$8\r\nreturn 1\r\n$1\r\n0\r\n");
  assert.equal(encodeCommand(["SET", "k", "é"]).toString("utf8"), "*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$2\r\né\r\n");
  const reply = Buffer.from("*2\r\n:1\r\n:0\r\n+OK\r\n$-1\r\n-ERR boom\r\n$3\r\nabc\r\n");
  let offset = 0;
  const values = [];
  for (;;) {
    const parsed = parseReply(reply, offset);
    if (!parsed) break;
    values.push(parsed.value);
    offset = parsed.offset;
  }
  assert.deepEqual(values.slice(0, 4), [[1, 0], "OK", null, values[3]]);
  assert.ok(values[3] instanceof RedisReplyError);
  assert.equal(values[4], "abc");
  assert.equal(parseReply(Buffer.from("*2\r\n:1\r\n"), 0), undefined);
  assert.equal(parseReply(Buffer.from("$5\r\nab"), 0), undefined);
});

test("an unreachable Redis fails a command fast, so the limiter falls back instead of waiting", async () => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  const store = createRedisStore({ host: "127.0.0.1", port, commandTimeoutMs: 100 });
  const limiter = createRateLimiter({ store, fallback: createMemoryStore() });
  const started = performance.now();
  for (let i = 0; i < 5; i++) assert.equal((await limiter.take("read", { sub: "u", orgId: "o", workspaceId: "w" })).store, "memory-fallback");
  // OD-07: a write is refused, just as fast, rather than admitted on a per-replica bucket.
  assert.equal((await limiter.take("write", { sub: "u", orgId: "o", workspaceId: "w" })).unavailable, true);
  assert.ok(performance.now() - started < 1_500, `took ${performance.now() - started} ms`);
  limiter.close();
});

// The verifier's OD-07 repro (2026-10-17), on the limiter a deployed gateway builds for itself:
// 15 writes, 25 model starts and 5 reads per Redis state.
async function od07Probe(t, redisUrl) {
  const config = loadConfig(cloudEnv(t, { OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: redisUrl }));
  const { base, calls, gateway } = await startGateway(t, { config, rateLimiter: defaultRateLimiter(config, () => {}) });
  const send = (method, path) => fetch(`${base}${path}`, {
    method, headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json" }, body: method === "GET" ? undefined : "{}",
  }).then(async (response) => ({ status: response.status, retryAfter: response.headers.get("retry-after"), body: await response.json() }));
  const counts = {};
  for (const [method, path, rateClass, n] of [["POST", "/workspace/agent-teams", "write", 15], ["POST", "/workspace/conversations", "model-start", 25], ["GET", "/workspace/usage", "read", 5]]) {
    for (let i = 0; i < n; i++) {
      const answer = await send(method, path);
      counts[`${rateClass} ${answer.status}`] = (counts[`${rateClass} ${answer.status}`] ?? 0) + 1;
      if (answer.status === 503) {
        assert.equal(answer.retryAfter, "2");
        assert.equal(answer.body.error, "omega_rate_limit_unavailable");
      }
    }
  }
  return { counts, reachedEe: calls.upstream.length, stats: gateway.rateLimiter.stats };
}

test("OD-07 on the default limiter: with Redis at a closed port, writes and model starts are 503 and never reach EE; reads fall back", async (t) => {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  const { counts, reachedEe, stats } = await od07Probe(t, `redis://127.0.0.1:${port}`);
  assert.deepEqual(counts, { "write 503": 15, "model-start 503": 25, "read 200": 5 });
  assert.equal(reachedEe, 5);
  assert.equal(stats.unavailable, 40);
  assert.equal(stats.fallbacks, 5);
});

test("OD-07 on the default limiter: a wedged Redis (accepts, never answers) refuses writes and model starts too", async (t) => {
  const sockets = [];
  const server = net.createServer((socket) => { sockets.push(socket); socket.resume(); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
  });
  const { counts, reachedEe } = await od07Probe(t, `redis://127.0.0.1:${server.address().port}`);
  assert.deepEqual(counts, { "write 503": 15, "model-start 503": 25, "read 200": 5 });
  assert.equal(reachedEe, 5);
});

test("a Redis that accepts but never answers is cut at the command deadline, and the wedged connection is dropped", async (t) => {
  const sockets = [];
  let serverSideClosed;
  const closedOnServer = new Promise((resolve) => { serverSideClosed = resolve; });
  const server = net.createServer((socket) => { sockets.push(socket); socket.resume(); socket.once("close", serverSideClosed); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const client = createRedisClient({ host: "127.0.0.1", port: server.address().port, commandTimeoutMs: 100 });
  t.after(async () => {
    client.close();
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
  });
  const started = performance.now();
  await assert.rejects(client.command(["PING"]));
  assert.ok(performance.now() - started < 600);
  // The client hung up, so a dead socket never keeps a request (or the process) waiting.
  await Promise.race([closedOnServer, new Promise((_, reject) => setTimeout(() => reject(new Error("wedged connection kept open")), 1_000).unref())]);
  for (let i = 0; i < 50 && client.connected(); i++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(client.connected(), false);
});

test("live Redis: the Lua bucket spends a burst, refuses with a wait, and refills", live, async (t) => {
  const store = createRedisStore(redisOptions());
  t.after(() => store.close());
  const identity = { sub: `u-${Date.now()}`, orgId: "o", workspaceId: `w-${Math.random()}` };
  const buckets = bucketsFor("write", identity);
  for (let i = 0; i < 10; i++) assert.equal((await store.take(buckets)).allowed, true, `burst ${i}`);
  const refused = await store.take(buckets);
  assert.equal(refused.allowed, false);
  assert.ok(refused.retryAfterMs > 0 && refused.retryAfterMs <= 200, `wait ${refused.retryAfterMs}`);
  await new Promise((resolve) => setTimeout(resolve, 450));
  assert.equal((await store.take(buckets)).allowed, true);
  // Keys expire once a bucket would be full again.
  const ttl = await store.client.command(["PTTL", buckets[0].key]);
  assert.ok(ttl > 0 && ttl <= 3_000, `ttl ${ttl}`);
});

test("live Redis: two gateway replicas share one limit (REL4)", live, async (t) => {
  const config = loadConfig(cloudEnv(t, { OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: `${REDIS_URL}/9` }));
  assert.equal(config.rateLimitStore.kind, "redis");
  const one = await startGateway(t, { config, rateLimiter: createRateLimiter({ store: createRedisStore(config.rateLimitStore) }) });
  const two = await startGateway(t, { config, rateLimiter: createRateLimiter({ store: createRedisStore(config.rateLimitStore) }) });
  const claims = { sub: crypto.randomUUID(), workspace_id: crypto.randomUUID() };
  const send = (base) => fetch(`${base}/workspace/capabilities`, { headers: { authorization: `Bearer ${mintAssertion(claims)}` } })
    .then(async (response) => { await response.arrayBuffer(); return response; });
  const started = performance.now();
  const responses = await Promise.all(Array.from({ length: 50 }, (_, i) => send(i % 2 ? one.base : two.base))
    .concat(Array.from({ length: 50 }, (_, i) => send(i % 2 ? two.base : one.base))));
  const elapsedS = (performance.now() - started) / 1000;
  const allowed = responses.filter((response) => response.status === 200).length;
  t.diagnostic(`two replicas, 100 reads in ${elapsedS.toFixed(3)} s: ${allowed} admitted, ${100 - allowed} refused`);
  // One shared bucket: the burst of 60 plus refill, never the 120 two private buckets allow.
  assert.ok(allowed >= 60 && allowed <= 60 + Math.ceil(elapsedS * 30) + 1, `admitted ${allowed}`);
  assert.equal(one.calls.upstream.length + two.calls.upstream.length, allowed);
  for (const replica of [one, two]) assert.equal(replica.gateway.rateLimiter.stats.fallbacks, 0);
  assert.ok(responses.filter((response) => response.status === 429).every((response) => Number(response.headers.get("retry-after")) >= 1));
});

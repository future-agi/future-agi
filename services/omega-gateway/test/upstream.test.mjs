// The call to EE (READINESS 1.3, S06, LD-05): responses piped, timeouts by class, the body read
// before the mint, a presented assertion limited before its body, and Django's refusals kept.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { startChildGateway } from "./child.mjs";
import { claimsOf, cloudEnv, json, mintAssertion, mintKeyAssertion, startGateway } from "./helpers.mjs";

const configFor = (t, extra) => loadConfig(cloudEnv(t, extra));

test("a 200 MB download is piped: the gateway process stays under 150 MB RSS (LD-05)", async (t) => {
  const TOTAL = 200 * 1024 * 1024;
  const CHUNK = Buffer.alloc(64 * 1024, 0x61);
  const ee = http.createServer((request, response) => {
    if (request.url === "/exchange") {
      response.writeHead(405).end();
      return;
    }
    response.writeHead(200, { "content-type": "application/zip", "content-disposition": "attachment; filename=agent.zip", "content-length": String(TOTAL) });
    let sent = 0;
    const pump = () => {
      while (sent < TOTAL) {
        sent += CHUNK.length;
        if (!response.write(CHUNK)) return response.once("drain", pump);
      }
      response.end();
    };
    pump();
  });
  await new Promise((resolve) => ee.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { ee.closeAllConnections(); ee.close(resolve); }));
  const eeUrl = `http://127.0.0.1:${ee.address().port}`;
  const gateway = await startChildGateway(t, { OMEGA_REMOTE_BASE_URL: eeUrl, OMEGA_SESSION_EXCHANGE_URL: `${eeUrl}/exchange` });
  const idleKb = gateway.rssKb();
  let peakKb = idleKb;
  const sampler = setInterval(() => { try { peakKb = Math.max(peakKb, gateway.rssKb()); } catch {} }, 50);
  t.after(() => clearInterval(sampler));
  const response = await fetch(`${gateway.base}/workspace/workforce/agents/a-1/export`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-length"), String(TOTAL));
  assert.equal(response.headers.get("content-disposition"), "attachment; filename=agent.zip");
  let received = 0;
  const reader = response.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.length;
    // A slow reader: backpressure must hold the gateway's buffers small.
    if (received % (16 * 1024 * 1024) < value.length) await new Promise((resolve) => setTimeout(resolve, 20));
  }
  clearInterval(sampler);
  assert.equal(received, TOTAL);
  t.diagnostic(`gateway RSS idle ${(idleKb / 1024).toFixed(1)} MB, peak ${(peakKb / 1024).toFixed(1)} MB during a ${TOTAL / 1048576} MB download`);
  assert.ok(peakKb < 150 * 1024, `peak RSS ${(peakKb / 1024).toFixed(1)} MB`);
});

test("timeout classes: a read waits for EE headers up to its class, then 504; a stream that started stays open", async (t) => {
  const { base } = await startGateway(t, {
    config: configFor(t, { OMEGA_REQUEST_TIMEOUT_MS: "1000" }),
    handler: {
      upstream: (url, init) => {
        if (url.endsWith("/usage")) return new Promise((resolve, reject) => init.signal.addEventListener("abort", () => reject(new Error("aborted"))));
        let controller;
        const stream = new ReadableStream({ start(c) { controller = c; c.enqueue(new TextEncoder().encode("id: 1\ndata: a\n\n")); } });
        setTimeout(() => { controller.enqueue(new TextEncoder().encode("id: 2\ndata: b\n\n")); controller.close(); }, 1_600);
        return new Response(stream, { headers: { "content-type": "text/event-stream" } });
      },
    },
  });
  const started = performance.now();
  const slow = await fetch(`${base}/workspace/usage`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(slow.status, 504);
  assert.equal((await slow.json()).error, "omega_remote_timeout");
  assert.ok(performance.now() - started < 2_500);
  const stream = await fetch(`${base}/workspace/conversations/c-1/events`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  const text = await stream.text();
  assert.match(text, /id: 2/, "the stream outlived the 1 s header timeout");
});

test("a stranger cannot make the gateway read a body: the caller is proven before the upload", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t), handler: { exchange: () => json({ code: "authentication_failed" }, 401) } });
  // A body far over the cap from an unknown browser token: the refusal is 401 (no session),
  // not 413, because the gateway asked the issuer before reading anything.
  const response = await fetch(`${base}/workspace/deployments`, { method: "POST", body: Buffer.alloc(80 * 1024 * 1024), headers: { authorization: "Bearer stolen-or-garbage" } });
  assert.equal(response.status, 401);
  assert.equal(calls.exchange.length, 1);
  assert.equal(calls.upstream.length, 0);
});

test("a slow upload never spends an aged key: the key is minted again after the body", async (t) => {
  const now = Math.floor(Date.now() / 1000);
  let minted = 0;
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      // The first key is already 30 s old (15 s left); the second is fresh.
      exchange: () => json({ accessToken: minted++ === 0 ? mintAssertion({ iat: now - 30, exp: now + 15 }) : mintAssertion() }),
      upstream: (url, init) => json({ received: init.body.length }),
    },
  });
  const response = await fetch(`${base}/workspace/deployments`, { method: "POST", body: Buffer.alloc(10_000, 1), headers: { authorization: "Bearer browser", "content-type": "application/x-tar" } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { received: 10_000 });
  assert.equal(calls.exchange.length, 2);
  const sent = calls.upstream[0].init.headers.authorization.slice("Bearer ".length);
  const claims = JSON.parse(Buffer.from(sent.slice("omega_delegate_".length).split(".")[1], "base64url").toString("utf8"));
  assert.ok(claims.exp - now >= 40, "EE received the fresh key");
  // A fresh first key is used as is: one mint per request.
  const again = await fetch(`${base}/workspace/deployments`, { method: "POST", body: "x", headers: { authorization: "Bearer browser" } });
  assert.equal(again.status, 200);
  assert.equal(calls.exchange.length, 3);
});

test("a slow upload by API key never spends an aged key either: the key is exchanged again after the body (SB W2-KEYS)", async (t) => {
  const now = Math.floor(Date.now() / 1000);
  let minted = 0;
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      // The verifier's case: the first key has 15 s left once the body is read; the second is fresh.
      exchange: () => json({ accessToken: minted++ === 0 ? mintKeyAssertion({ iat: now - 30, exp: now + 15 }) : mintKeyAssertion() }),
      upstream: (url, init) => json({ received: init.body.length }),
    },
  });
  const key = { "x-api-key": "pub-123", "x-secret-key": "secret-123" };
  const response = await fetch(`${base}/workspace/sandboxes/sb-1/files/data.bin`, { method: "PUT", body: Buffer.alloc(10_000, 1), headers: { ...key, "content-type": "application/octet-stream" } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { received: 10_000 });
  assert.equal(calls.exchange.length, 2);
  for (const exchange of calls.exchange) {
    assert.equal(exchange.init.headers["x-api-key"], "pub-123");
    assert.equal(exchange.init.headers["x-secret-key"], "secret-123");
  }
  const claims = claimsOf(calls.upstream[0].init.headers.authorization.slice("Bearer ".length));
  assert.ok(claims.exp - now >= 40, "EE received the fresh key");
  assert.equal(claims.auth_method, "api_key");
  assert.equal(calls.upstream[0].init.headers["x-api-key"], undefined);
});

test("an API-key upload trickled past its first key's expiry reaches EE with a fresh key, never an expired one", async (t) => {
  let minted = 0;
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: () => {
        const now = Math.floor(Date.now() / 1000);
        return json({ accessToken: minted++ === 0 ? mintKeyAssertion({ iat: now - 43, exp: now + 2 }) : mintKeyAssertion() });
      },
      upstream: (url, init) => json({ received: init.body.length }),
    },
  });
  const port = new URL(base).port;
  const answer = await new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port, method: "PUT", path: "/workspace/sandboxes/sb-1/files/slow.bin", headers: { "x-api-key": "pub-123", "x-secret-key": "secret-123", "content-type": "application/octet-stream" } }, (response) => {
      const parts = [];
      response.on("data", (chunk) => parts.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(parts).toString("utf8")) }));
    });
    request.on("error", reject);
    let sent = 0;
    const next = () => {
      if (sent === 6) return request.end();
      sent += 1;
      request.write(Buffer.alloc(10, 1));
      setTimeout(next, 600);
    };
    next();
  });
  assert.equal(answer.status, 200);
  assert.deepEqual(answer.body, { received: 60 });
  assert.equal(calls.exchange.length, 2);
  const sentAt = Math.floor(calls.upstream[0].at / 1000);
  const claims = claimsOf(calls.upstream[0].init.headers.authorization.slice("Bearer ".length));
  assert.ok(claims.exp - sentAt >= 40, `EE received ${claims.exp - sentAt} s of key`);
});

// The verifier's round-2 drift case (r2/attack.test.mjs): the issuer's second answer, after a
// slow upload, speaks for someone or somewhere else. The first key is aged (15 s left once the
// body is read), so the gateway mints again; the second must name the same person, tenant and
// credential, or the request is refused and EE never sees it.
const OTHER = "99999999-9999-4999-8999-999999999999";
for (const [label, presented, first, second] of [
  ["an API key, another workspace", "key", mintKeyAssertion, () => mintKeyAssertion({ workspace_id: OTHER })],
  ["an API key, another organization", "key", mintKeyAssertion, () => mintKeyAssertion({ org_id: OTHER })],
  ["an API key, another user", "key", mintKeyAssertion, () => mintKeyAssertion({ sub: OTHER })],
  ["an API key, another key", "key", mintKeyAssertion, () => mintKeyAssertion({ key_id: OTHER })],
  ["a browser token, another workspace", "browser", mintAssertion, () => mintAssertion({ workspace_id: OTHER })],
  ["a browser token, another session", "browser", mintAssertion, () => mintAssertion({ sid: OTHER })],
]) {
  test(`a key minted again after a slow upload must speak for the same identity: ${label} is refused`, async (t) => {
    const now = Math.floor(Date.now() / 1000);
    let minted = 0;
    const { base, calls } = await startGateway(t, {
      config: configFor(t),
      handler: { exchange: () => json({ accessToken: minted++ === 0 ? first({ iat: now - 30, exp: now + 15 }) : second() }) },
    });
    const headers = presented === "key" ? { "x-api-key": "pub-123", "x-secret-key": "secret-123" } : { authorization: "Bearer fa-browser-token" };
    const response = await fetch(`${base}/workspace/sandboxes/sb-1/files/data.bin`, { method: "PUT", body: Buffer.alloc(1000, 1), headers: { ...headers, "content-type": "application/octet-stream" } });
    assert.equal(response.status, 403);
    const body = await response.json();
    assert.deepEqual([body.error, body.reason], ["omega_scope_mismatch", "scope_mismatch"]);
    assert.equal(calls.exchange.length, 2);
    assert.equal(calls.upstream.length, 0, "nothing reached EE");
  });
}

test("a key minted again with the same identity is forwarded even when its role or products changed (both are checked again)", async (t) => {
  const now = Math.floor(Date.now() / 1000);
  let minted = 0;
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: () => json({ accessToken: minted++ === 0 ? mintKeyAssertion({ iat: now - 30, exp: now + 15 }) : mintKeyAssertion({ role: "admin", products: { sandboxes: "active" } }) }),
      upstream: (url, init) => json({ received: init.body.length }),
    },
  });
  const response = await fetch(`${base}/workspace/sandboxes/sb-1/files/data.bin`, { method: "PUT", body: Buffer.alloc(1000, 1), headers: { "x-api-key": "pub-123", "x-secret-key": "secret-123", "content-type": "application/octet-stream" } });
  assert.equal(response.status, 200);
  assert.equal(calls.upstream.length, 1);
  assert.equal(claimsOf(calls.upstream[0].init.headers.authorization.slice("Bearer ".length)).role, "admin");
});

test("a presented assertion is refused or limited before its body is read", async (t) => {
  const limiter = createRateLimiter({ classes: { read: [{ scope: "user", capacity: 1, perSecond: 0.001 }], write: [{ scope: "user", capacity: 1, perSecond: 0.001 }], "model-start": [{ scope: "user", capacity: 1, perSecond: 0.001 }], "public-share": [{ scope: "ip", capacity: 1, perSecond: 1 }] } });
  const { base, calls } = await startGateway(t, { config: configFor(t, { OMEGA_MAX_BUNDLE_BYTES: "1048576" }), rateLimiter: limiter });
  const assertion = () => mintAssertion();
  assert.equal((await fetch(`${base}/workspace/deployments`, { method: "POST", body: "x", headers: { authorization: `Bearer ${assertion()}` } })).status, 200);
  // Over the limit with a body over the cap: the answer is 429, not 413, because nothing was read.
  const limited = await fetch(`${base}/workspace/deployments`, { method: "POST", body: Buffer.alloc(2_000_000), headers: { authorization: `Bearer ${assertion()}` } });
  assert.equal(limited.status, 429);
  assert.equal(calls.upstream.length, 1);
});

test("a presented assertion that a slow upload outlived is refused with the retryable code, never sent to EE expired", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const port = new URL(base).port;
  // The verifier's repro: a key with seconds left, a deployment body trickled past its expiry.
  const trickle = (token, chunks, gapMs) => new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port, method: "POST", path: "/workspace/deployments", headers: { authorization: `Bearer ${token}`, "content-type": "application/x-tar" } }, (response) => {
      const parts = [];
      response.on("data", (chunk) => parts.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(parts).toString("utf8")) }));
    });
    request.on("error", reject);
    let sent = 0;
    const next = () => {
      if (sent === chunks) return request.end();
      sent += 1;
      request.write(Buffer.alloc(10, 1));
      setTimeout(next, gapMs);
    };
    next();
  });
  const now = Math.floor(Date.now() / 1000);
  const aged = await trickle(mintAssertion({ iat: now - 43, exp: now + 2 }), 6, 500);
  assert.equal(aged.status, 401);
  assert.equal(aged.body.error, "omega_assertion_rejected");
  // `expired_assertion` is what omegaClient.js retries with a fresh key (STALE_ASSERTION_CODES).
  assert.equal(aged.body.reason, "expired_assertion");
  assert.equal(calls.upstream.length, 0);
  // The same short-lived kind of key with a body that arrives at once is forwarded.
  const later = Math.floor(Date.now() / 1000);
  const quick = await trickle(mintAssertion({ iat: later - 40, exp: later + 5 }), 1, 0);
  assert.equal(quick.status, 200);
  assert.equal(calls.upstream.length, 1);
});

test("Django's refusals keep their meaning: viewer (P10), throttled (429 + Retry-After), Future AGI down (503)", async (t) => {
  for (const [answer, status, error, retryAfter] of [
    [() => json({ code: "omega_viewer_not_admitted", detail: "x" }, 403), 403, "omega_viewer_not_admitted", null],
    [() => json({ code: "permission_denied" }, 403), 401, "omega_session_unavailable", null],
    [() => json({ code: "throttled" }, 429, { "retry-after": "17" }), 429, "omega_rate_limited", "17"],
    [() => json({ code: "error" }, 503), 503, "omega_issuer_unavailable", null],
    [() => { throw new Error("connect ECONNREFUSED"); }, 503, "omega_issuer_unavailable", null],
  ]) {
    const { base, calls } = await startGateway(t, { config: configFor(t), handler: { exchange: answer } });
    const response = await fetch(`${base}/workspace/capabilities`, { headers: { authorization: "Bearer browser" } });
    assert.equal(response.status, status, error);
    const body = await response.json();
    assert.equal(body.error, error);
    assert.equal(response.headers.get("retry-after"), retryAfter);
    if (status === 429) assert.equal(body.rateClass, "session-exchange");
    assert.equal(calls.upstream.length, 0);
  }
});

test("an unreachable EE is 502 and a browser that leaves early cancels a read", async (t) => {
  let aborted = false;
  const { base } = await startGateway(t, {
    config: configFor(t),
    handler: {
      upstream: (url, init) => {
        if (url.endsWith("/usage")) throw new Error("ECONNREFUSED");
        return new Promise((resolve, reject) => init.signal.addEventListener("abort", () => { aborted = true; reject(new Error("aborted")); }));
      },
    },
  });
  const down = await fetch(`${base}/workspace/usage`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(down.status, 502);
  assert.equal((await down.json()).error, "omega_remote_unreachable");
  const controller = new AbortController();
  const pending = fetch(`${base}/workspace/capabilities`, { headers: { authorization: `Bearer ${mintAssertion()}` }, signal: controller.signal }).catch(() => null);
  await new Promise((resolve) => setTimeout(resolve, 100));
  controller.abort();
  await pending;
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.equal(aborted, true);
});

test("a gzipped EE answer reaches the browser whole: no stale length, no hang", async (t) => {
  const zlib = await import("node:zlib");
  const payload = JSON.stringify({ items: Array.from({ length: 5_000 }, (_, i) => ({ i, text: "repeated text compresses well" })) });
  const gzipped = zlib.gzipSync(payload);
  const ee = http.createServer((request, response) => {
    if (request.url === "/exchange") return response.writeHead(405).end();
    response.writeHead(200, { "content-type": "application/json", "content-encoding": "gzip", "content-length": String(gzipped.length) });
    response.end(gzipped);
  });
  await new Promise((resolve) => ee.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { ee.closeAllConnections(); ee.close(resolve); }));
  const eeUrl = `http://127.0.0.1:${ee.address().port}`;
  const gateway = await startChildGateway(t, { OMEGA_REMOTE_BASE_URL: eeUrl, OMEGA_SESSION_EXCHANGE_URL: `${eeUrl}/exchange` });
  const response = await fetch(`${gateway.base}/workspace/usage`, { headers: { authorization: `Bearer ${mintAssertion()}` }, signal: AbortSignal.timeout(5_000) });
  assert.equal(response.status, 200);
  // fetch decoded the body, so the gateway must not repeat EE's compressed length or encoding.
  assert.equal(response.headers.get("content-encoding"), null);
  assert.notEqual(response.headers.get("content-length"), String(gzipped.length));
  assert.equal(await response.text(), payload);
});

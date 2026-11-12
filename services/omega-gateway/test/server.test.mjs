import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { claimsOf, cloudEnv, json, mintAssertion, mintKeyAssertion, startGateway } from "./helpers.mjs";

// Adapted from Squad: the gateway now checks every upstream credential, so the
// stub issuer returns assertions in the real wire format instead of opaque strings.
const VERIFIED = mintAssertion();
const issued = [mintAssertion(), mintAssertion()];
async function withGateway(t, fetchImpl) {
  const config = loadConfig(cloudEnv(t, { OMEGA_ALLOWED_ORIGINS: "https://app.example.test" }));
  const { base } = await startGateway(t, { config, handler: { exchange: (init) => fetchImpl(config.exchangeUrl, init), upstream: (url, init) => fetchImpl(url, init) } });
  return { base, config };
}

test("workspace selections go to the issuer while only its credential reaches Omega", async t => {
  const calls = [];
  const { base, config } = await withGateway(t, async (url, init) => {
    calls.push({ url, init });
    return calls.length === 1 ? json({ accessToken: VERIFIED }) : json({ conversations: [] });
  });
  const response = await fetch(`${base}/workspace/conversations`, {
    headers: {
      Authorization: "Bearer browser-session",
      "X-Organization-Id": "requested-org",
      "X-Workspace-Id": "requested-workspace",
      "X-Omega-Workspace": "spoofed-native-workspace",
      "X-Omega-Principal-Roles": "operator",
      "Last-Event-ID": "17",
    },
  });
  assert.equal(response.status, 200);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, config.exchangeUrl);
  assert.deepEqual(JSON.parse(calls[0].init.body), { audience: "omega-squad" });
  assert.equal(calls[0].init.headers.authorization, "Bearer browser-session");
  assert.equal(calls[0].init.headers["x-organization-id"], "requested-org");
  assert.equal(calls[0].init.headers["x-workspace-id"], "requested-workspace");
  const { traceparent, "x-request-id": requestId, ...forwarded } = calls[1].init.headers;
  assert.deepEqual(forwarded, { accept: "*/*", authorization: `Bearer ${VERIFIED}`, "last-event-id": "17" });
  // One trace across the hops: the issuer and EE get the same trace and request id.
  assert.equal(calls[0].init.headers.traceparent, traceparent);
  assert.equal(requestId, response.headers.get("x-request-id"));
  assert.equal(traceparent.split("-")[1], requestId);
});

test("API-key headers never reach the issuer or Omega", async t => {
  const calls = [];
  const { base } = await withGateway(t, async (url, init) => {
    calls.push({ url, init });
    return json({ accessToken: VERIFIED });
  });
  const response = await fetch(`${base}/workspace/capabilities`, {
    headers: {
      Authorization: "Bearer browser-session",
      "X-Api-Key": "public-key-id",
      "X-Secret-Key": "secret-that-must-not-leave-the-client",
    },
  });
  assert.equal(response.status, 401);
  assert.equal(calls.length, 0);
});

const KEY_VERIFIED = mintKeyAssertion();

test("a personal API key exchanges at the issuer and never reaches Omega", async t => {
  const calls = [];
  const { base, config } = await withGateway(t, async (url, init) => {
    calls.push({ url, init });
    if (url === config.exchangeUrl) return json({ accessToken: KEY_VERIFIED });
    return json({ available: true });
  });
  const response = await fetch(`${base}/workspace/capabilities`, {
    headers: {
      "X-Api-Key": "public-key-id",
      "X-Secret-Key": "secret-that-must-not-reach-ee",
    },
  });
  assert.equal(response.status, 200);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].init.headers["x-api-key"], "public-key-id");
  assert.equal(calls[0].init.headers["x-secret-key"], "secret-that-must-not-reach-ee");
  assert.equal(calls[0].init.headers.authorization, undefined);
  assert.equal(calls[1].init.headers.authorization, `Bearer ${KEY_VERIFIED}`);
  assert.equal(calls[1].init.headers["x-api-key"], undefined);
  assert.equal(calls[1].init.headers["x-secret-key"], undefined);
  assert.equal(JSON.stringify(calls[1]).includes("secret-that-must-not-reach-ee"), false);
});

test("the exchange names the client the gateway saw, as one address, never the caller's chain", async t => {
  // Django limits key attempts per key and client source (SB W2-KEYS); a caller must not choose
  // its own source by sending X-Forwarded-For, and EE never receives the header.
  for (const [hops, expected] of [["0", /^(::ffff:)?127\.0\.0\.1$|^::1$/], ["1", /^198\.51\.100\.1$/], ["2", /^203\.0\.113\.9$/]]) {
    const calls = [];
    const config = loadConfig(cloudEnv(t, { OMEGA_ALLOWED_ORIGINS: "https://app.example.test", OMEGA_GATEWAY_TRUSTED_PROXY_HOPS: hops }));
    const fetchImpl = async (url, init) => {
      calls.push({ url, init });
      return url === config.exchangeUrl ? json({ accessToken: KEY_VERIFIED }) : json({ available: true });
    };
    const { base } = await startGateway(t, { config, handler: { exchange: (init) => fetchImpl(config.exchangeUrl, init), upstream: (url, init) => fetchImpl(url, init) } });
    const response = await fetch(`${base}/workspace/capabilities`, {
      headers: { "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123", "X-Forwarded-For": "203.0.113.9, 198.51.100.1" },
    });
    assert.equal(response.status, 200, `hops ${hops}`);
    const sent = calls[0].init.headers["x-forwarded-for"];
    assert.match(sent, expected, `hops ${hops}`);
    assert.equal(sent.includes(","), false, `hops ${hops}: a chain reached the issuer`);
    assert.equal(calls[1].init.headers["x-forwarded-for"], undefined, `hops ${hops}: EE got the client address`);
  }
});

test("a key put in the URL is refused before anything is called, in every spelling", async t => {
  const calls = [];
  const { base } = await withGateway(t, async (url, init) => { calls.push({ url, init }); return json({ accessToken: KEY_VERIFIED }); });
  for (const name of ["secret_key", "SECRET_KEY", "secretkey", "secret-key", "x-api-key", "X-Secret-Key", "api-key", "api_key", "apikey"]) {
    const response = await fetch(`${base}/workspace/capabilities?${name}=SECRET-must-not-leak-0xdeadbeef`, {
      headers: { "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123" },
    });
    assert.equal(response.status, 400, name);
    assert.equal((await response.json()).error, "credential_in_query", name);
  }
  assert.equal(calls.length, 0, "the issuer or EE saw a URL carrying a key");
});

test("a minted assertion must name the method of the credential the gateway exchanged", async t => {
  // An issuer bug or a confused deputy: a key exchange signed as a browser session, and the reverse.
  for (const [headers, minted, label] of [
    [{ "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123" }, mintAssertion(), "api key answered with a session"],
    [{ Authorization: "Bearer browser-session" }, mintKeyAssertion(), "browser token answered with an api key"],
  ]) {
    const calls = [];
    const { base, config } = await withGateway(t, async (url, init) => {
      calls.push({ url, init });
      return url === config.exchangeUrl ? json({ accessToken: minted }) : json({ available: true });
    });
    const response = await fetch(`${base}/workspace/capabilities`, { headers });
    assert.equal(response.status, 401, label);
    const body = await response.json();
    assert.equal(body.error, "omega_session_unavailable", label);
    assert.equal(body.reason, "minted_auth_method_mismatch", label);
    assert.deepEqual(calls.map((call) => call.url), [config.exchangeUrl], label);
  }
});

test("an API key exchange keeps Django's coded refusals", async t => {
  for (const [status, code, expected] of [
    [403, "omega_key_not_admitted", [403, "omega_key_not_admitted"]],
    [403, "omega_viewer_not_admitted", [403, "omega_viewer_not_admitted"]],
    [404, "product_not_admitted", [404, "product_not_admitted"]],
    [503, "verification_unavailable", [503, "verification_unavailable"]],
    [500, undefined, [503, "omega_issuer_unavailable"]],
    [401, undefined, [401, "omega_session_unavailable"]],
  ]) {
    const calls = [];
    const { base, config } = await withGateway(t, async (url, init) => {
      calls.push(url);
      return url === config.exchangeUrl ? json(code ? { code } : { detail: "nope" }, status) : json({ available: true });
    });
    const response = await fetch(`${base}/workspace/capabilities`, { headers: { "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123" } });
    assert.deepEqual([response.status, (await response.json()).error], expected, `${status} ${code}`);
    assert.equal(calls.length, 1, `${status} ${code} reached EE`);
  }
  const throttled = await withGateway(t, async () => json({ code: "throttled" }, 429, { "retry-after": "7" }));
  const limited = await fetch(`${throttled.base}/workspace/capabilities`, { headers: { "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123" } });
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), "7");
  assert.equal((await limited.json()).rateClass, "session-exchange");
});

test("the EE request of an API-key caller carries only the key id, never the key", async t => {
  let forwarded;
  const { base, config } = await withGateway(t, async (url, init) => {
    if (url === config.exchangeUrl) return json({ accessToken: KEY_VERIFIED });
    forwarded = init;
    return json({ available: true });
  });
  assert.equal((await fetch(`${base}/workspace/capabilities`, { headers: { "X-Api-Key": "pub-123", "X-Secret-Key": "secret-123" } })).status, 200);
  const claims = claimsOf(forwarded.headers.authorization.slice("Bearer ".length));
  assert.equal(claims.auth_method, "api_key");
  assert.equal(claims.key_id, "55555555-5555-4555-8555-555555555555");
  assert.equal(Object.hasOwn(claims, "sid"), false);
  assert.equal(JSON.stringify(forwarded).includes("pub-123"), false);
  assert.equal(JSON.stringify(forwarded).includes("secret-123"), false);
});

test("an issuer's membership refusal prevents any request to Omega", async t => {
  const calls = [];
  const { base, config } = await withGateway(t, async (url, init) => { calls.push({url, init}); return json({ error: "workspace_denied" }, 403); });
  const response = await fetch(`${base}/workspace/workforce`, { headers: {
    Authorization: "Bearer browser-session", "X-Organization-Id": "foreign-org", "X-Workspace-Id": "foreign-workspace",
  } });
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "omega_session_unavailable");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, config.exchangeUrl);
});

test("each request obtains a fresh one-use credential without exposing it to the browser", async t => {
  let minted = 0;
  const presented = [];
  const { base } = await withGateway(t, async (url, init) => {
    if (url.includes("session-exchange")) return json({ accessToken: issued[minted++] });
    presented.push(init.headers.authorization);
    return json({ available: true });
  });
  for (let i = 0; i < 2; i++) {
    const response = await fetch(`${base}/workspace/capabilities`, { headers: { Authorization: "Bearer browser-session" } });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal((await response.text()).includes("omega_delegate_"), false);
  }
  assert.deepEqual(presented, [`Bearer ${issued[0]}`, `Bearer ${issued[1]}`]);
});

test("agent drafting forwards its mutation key and aborts model work when the browser disconnects", async t => {
  let started;
  const upstreamStarted = new Promise(resolve => { started = resolve; });
  let aborted;
  const upstreamAborted = new Promise(resolve => { aborted = resolve; });
  let upstreamCalls = 0;
  const { base, config } = await withGateway(t, async (url, init) => {
    if (url === config.exchangeUrl) return json({ accessToken: mintAssertion({ role: "admin" }) });
    upstreamCalls += 1;
    assert.equal(url, `${config.remoteBaseUrl}/workspace/workforce/agent-drafts`);
    assert.equal(init.headers["idempotency-key"], "draft-request-1");
    assert.match(init.headers.authorization, /^Bearer omega_delegate_/);
    assert.deepEqual(JSON.parse(init.body), { message: "Draft an analyst" });
    started();
    return new Promise((resolve, reject) => init.signal.addEventListener("abort", () => {
      aborted(); reject(new Error("aborted"));
    }, { once: true }));
  });
  const request = http.request(`${base}/workspace/workforce/agent-drafts`, { method: "POST", headers: {
    authorization: "Bearer browser-session", "content-type": "application/json", "idempotency-key": "draft-request-1",
  } });
  request.on("error", () => {});
  request.end(JSON.stringify({ message: "Draft an analyst" }));
  await upstreamStarted;
  request.destroy();
  await Promise.race([upstreamAborted, new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("upstream was not canceled")), 1000); timer.unref(); })]);
  assert.equal(upstreamCalls, 1);
});

test("an upstream SSE failure closes that stream while the gateway continues serving", async t => {
  let streamController;
  const { base } = await withGateway(t, async url => {
    if (url.includes("session-exchange")) return json({ accessToken: mintAssertion() });
    if (url.endsWith("/events")) return new Response(new ReadableStream({ start(controller) {
      streamController = controller;
      controller.enqueue(new TextEncoder().encode("id: 1\nevent: status\ndata: {}\n\n"));
    } }), { headers: { "content-type": "text/event-stream" } });
    return json({ available: true });
  });
  const response = await fetch(`${base}/workspace/conversations/run-1/events`, { headers: { Authorization: "Bearer browser" } });
  const reader = response.body.getReader();
  assert.equal((await reader.read()).done, false);
  streamController.error(new Error("runtime restarted"));
  await assert.rejects(reader.read());
  const next = await fetch(`${base}/workspace/capabilities`, { headers: { Authorization: "Bearer browser" } });
  assert.equal(next.status, 200);
});

test("closing a browser SSE connection cancels its upstream stream", async t => {
  let canceled;
  const cancellation = new Promise(resolve => { canceled = resolve; });
  const { base } = await withGateway(t, async url => {
    if (url.includes("session-exchange")) return json({ accessToken: mintAssertion() });
    return new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode("data: ready\n\n")); },
      cancel() { canceled(); },
    }), { headers: { "content-type": "text/event-stream" } });
  });
  const response = await fetch(`${base}/workspace/conversations/run-1/events`, { headers: { Authorization: "Bearer browser" } });
  const reader = response.body.getReader(); await reader.read(); await reader.cancel();
  await Promise.race([cancellation, new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("upstream stream stayed open")), 1000); timer.unref(); })]);
});

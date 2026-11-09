// Human-session-only verbs (sandbox-better PLAN L3 and W2-ACCESS, critique C2-3 "API key can
// self-approve"; SB W2-KEYS fix round 2): an assertion that names `api_key` gets 403 on deciding
// an approval and on writing sandbox settings, before the limiter and the body, and nothing
// reaches EE; a browser session is forwarded as before, and a key still reads both.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { ROUTE_MANIFEST } from "../src/routes.mjs";
import { checkSessionOnlyRoutes, decideSessionOnly, SESSION_ONLY_ROUTES } from "../src/session-only.mjs";
import { claimsOf, cloudEnv, json, mintAssertion, mintKeyAssertion, startGateway } from "./helpers.mjs";

const configFor = (t, extra) => loadConfig(cloudEnv(t, extra));
const KEY = { "x-api-key": "pub-123", "x-secret-key": "secret-123" };
const APPROVAL = "/workspace/approvals/appr-1";
const SETTINGS = "/workspace/sandboxes/settings";
const SETTINGS_DOC = JSON.stringify({ settings: { defaultNetwork: "none" } });
const OBSERVE_SETTINGS = "/workspace/observe/settings";
const OBSERVE_DOC = JSON.stringify({ contentCapture: "on" });
const REFUSED = { error: "omega_human_session_required", reason: "session_only_verb" };

// Non-GET routes whose path names an approval decision, sharing, a share link or settings: the
// verbs W2-ACCESS reserves for a person. Today's manifest has two; W2-ACCESS adds its sharing and
// share-link routes to the byte-identical manifest, and this test then fails until the gateway
// table lists them.
const LOOKS_SESSION_ONLY = /\/approvals\/\{|shar(?:e|ing)|settings|\/otlp(?:\/test)?$/;
const lookalikes = (routes) => routes
  .filter((route) => route.method !== "GET" && LOOKS_SESSION_ONLY.test(route.path))
  .map((route) => `${route.method} ${route.path}`);

// Owner ruling X-1: minting the conversation computer's channel token is a person's act too.
const CHANNEL_MINT = "POST /workspace/conversations/{id}/computer/channel";

test("the table lists exactly the manifest's session-only verbs, and a write route that looks like one cannot be added unlisted", () => {
  const policy = ["GET /workspace/tool-policy/workspace", "PUT /workspace/tool-policy/workspace", "GET /workspace/tool-policy/organization", "PUT /workspace/tool-policy/organization"];
  assert.deepEqual([...SESSION_ONLY_ROUTES], ["POST /workspace/approvals/{id}", "PUT /workspace/sandboxes/settings", CHANNEL_MINT, "PUT /workspace/observe/settings", ...policy, "PUT /workspace/otlp", "POST /workspace/otlp/test"]);
  assert.deepEqual([...lookalikes(ROUTE_MANIFEST.routes), CHANNEL_MINT, ...policy].sort(), [...SESSION_ONLY_ROUTES].sort());
  // The one channel mint the manifest names is the one this table lists (channel-token.test.mjs binds it).
  assert.deepEqual(ROUTE_MANIFEST.routes.filter((route) => route.credential === "channel-mint").map((route) => `${route.method} ${route.path}`), [CHANNEL_MINT]);
  // The guard itself catches the routes W2-ACCESS will add, and leaves reads alone.
  assert.deepEqual(lookalikes([
    { method: "POST", path: "/workspace/sandboxes/{sandbox}/share-links" },
    { method: "PUT", path: "/workspace/sandboxes/{sandbox}/sharing/{id}" },
    { method: "GET", path: "/workspace/workforce/sharing-candidates" },
    { method: "GET", path: "/workspace/sandboxes/settings" },
    { method: "POST", path: "/workspace/deployments/{deployment}/access-token" },
  ]), ["POST /workspace/sandboxes/{sandbox}/share-links", "PUT /workspace/sandboxes/{sandbox}/sharing/{id}"]);
  // An entry the manifest does not admit stops the gateway at load.
  assert.throws(() => checkSessionOnlyRoutes(ROUTE_MANIFEST, ["POST /workspace/approval/{id}"]), /names no manifest route/);
  assert.doesNotThrow(() => checkSessionOnlyRoutes());
});

test("the decision fails closed: only auth_method session passes a listed route; unlisted routes are not judged", () => {
  const route = (method, path) => ROUTE_MANIFEST.match(method, path).route;
  const approve = route("POST", APPROVAL);
  assert.equal(decideSessionOnly(approve, { auth_method: "session" }), null);
  for (const claims of [{ auth_method: "api_key" }, { auth_method: "Session" }, { auth_method: undefined }, {}, null]) {
    assert.deepEqual(decideSessionOnly(approve, claims), { status: 403, ...REFUSED }, JSON.stringify(claims));
  }
  for (const [method, path] of [["GET", "/workspace/approvals"], ["GET", SETTINGS], ["GET", OBSERVE_SETTINGS], ["POST", "/workspace/sandboxes"], ["POST", "/workspace/sandboxes/sb-1/exec"]]) {
    assert.equal(decideSessionOnly(route(method, path), { auth_method: "api_key" }), null, `${method} ${path}`);
  }
});

test("through the server: an API key gets 403 on both verbs and EE sees nothing; a browser session is forwarded", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: { exchange: (init) => json({ accessToken: init.headers["x-api-key"] ? mintKeyAssertion() : mintAssertion() }) },
  });
  const send = (method, path, headers) => fetch(`${base}${path}`, {
    method, headers: { "content-type": "application/json", ...headers }, body: method === "GET" ? undefined : path === SETTINGS ? SETTINGS_DOC : JSON.stringify({ decision: "approve" }),
  });
  for (const [method, path] of [["POST", APPROVAL], ["PUT", SETTINGS]]) {
    const byKey = await send(method, path, KEY);
    assert.equal(byKey.status, 403, `${method} ${path}`);
    assert.deepEqual({ ...(await byKey.json()), requestId: undefined }, { ...REFUSED, requestId: undefined });
  }
  assert.equal(calls.upstream.length, 0, "no key request reached EE");
  assert.equal(calls.exchange.length, 2, "each key request was exchanged, then refused on its verified claims");

  // Reads stay open to a key: an agent polls its approval and reads its settings.
  for (const path of ["/workspace/approvals", SETTINGS]) assert.equal((await send("GET", path, KEY)).status, 200, path);
  assert.equal(calls.upstream.length, 2);
  for (const call of calls.upstream) assert.equal(claimsOf(call.init.headers.authorization.slice("Bearer ".length)).auth_method, "api_key");

  // The same verbs by a browser session reach EE with a session assertion.
  for (const [method, path] of [["POST", APPROVAL], ["PUT", SETTINGS]]) {
    assert.equal((await send(method, path, { authorization: "Bearer fa-browser-token" })).status, 200, `${method} ${path}`);
  }
  assert.equal(calls.upstream.length, 4);
  for (const call of calls.upstream.slice(2)) {
    const claims = claimsOf(call.init.headers.authorization.slice("Bearer ".length));
    assert.equal(claims.auth_method, "session");
    assert.equal(call.init.headers["x-api-key"], undefined);
  }
});

// P07 CHAT-TRACE (OWNER-RULINGS item 53 (d), Q3): the Observe content switch is admitted to owners and admins and
// decided by a person. An admin's API key gets 403 on the write and EE sees nothing; the key still reads the setting;
// an admin's browser session changes it; a member's session is refused by the route's admin floor.
test("the Observe settings write: an admin's key gets 403 and EE sees nothing; its session is forwarded; a member is below the floor", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: { exchange: (init) => json({ accessToken: init.headers["x-api-key"] ? mintKeyAssertion({ role: "admin" }) : init.headers.authorization === "Bearer fa-member-token" ? mintAssertion() : mintAssertion({ role: "admin" }) }) },
  });
  const send = (method, headers) => fetch(`${base}${OBSERVE_SETTINGS}`, { method, headers: { "content-type": "application/json", ...headers }, body: method === "GET" ? undefined : OBSERVE_DOC });
  const byKey = await send("PUT", KEY);
  assert.equal(byKey.status, 403);
  assert.deepEqual({ ...(await byKey.json()), requestId: undefined }, { ...REFUSED, requestId: undefined });
  assert.equal(calls.upstream.length, 0, "the key's write never reached EE");
  assert.equal((await send("GET", KEY)).status, 200, "a key reads the setting");
  assert.equal(calls.upstream.length, 1);
  assert.equal((await send("PUT", { authorization: "Bearer fa-browser-token" })).status, 200);
  assert.equal(calls.upstream.length, 2);
  assert.equal(claimsOf(calls.upstream[1].init.headers.authorization.slice("Bearer ".length)).auth_method, "session");
  const member = await send("PUT", { authorization: "Bearer fa-member-token" });
  assert.equal(member.status, 403);
  assert.equal((await member.json()).error, "omega_role_not_permitted");
  assert.equal(calls.upstream.length, 2, "a member's write never reached EE");
});

test("a presented assertion that says api_key is refused too: the decision reads the verified claims, not the header kind", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const response = await fetch(`${base}${APPROVAL}`, {
    method: "POST", headers: { authorization: `Bearer ${mintKeyAssertion()}`, "content-type": "application/json" }, body: "{}",
  });
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, REFUSED.error);
  assert.equal(calls.upstream.length, 0);
  // A presented session assertion on the same verb is forwarded.
  const allowed = await fetch(`${base}${APPROVAL}`, { method: "POST", headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json" }, body: "{}" });
  assert.equal(allowed.status, 200);
  assert.equal(calls.upstream.length, 1);
});

test("a refused verb spends no rate token and is answered before its body is read", async (t) => {
  // One write per user: if the key's refusal had spent it, the browser's write would be 429.
  const one = [{ scope: "user", capacity: 1, perSecond: 0.001 }];
  const limiter = createRateLimiter({ classes: { read: one, write: one, "model-start": one, "public-share": [{ scope: "ip", capacity: 1, perSecond: 1 }] } });
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    rateLimiter: limiter,
    handler: { exchange: (init) => json({ accessToken: init.headers["x-api-key"] ? mintKeyAssertion() : mintAssertion() }) },
  });
  for (let i = 0; i < 3; i++) {
    const byKey = await fetch(`${base}${SETTINGS}`, { method: "PUT", headers: { ...KEY, "content-type": "application/json" }, body: SETTINGS_DOC });
    assert.equal(byKey.status, 403);
  }
  // The same user (the helpers' sub) by browser: still has its one write.
  const byBrowser = await fetch(`${base}${SETTINGS}`, { method: "PUT", headers: { authorization: "Bearer fa-browser-token", "content-type": "application/json" }, body: SETTINGS_DOC });
  assert.equal(byBrowser.status, 200);
  assert.equal(calls.upstream.length, 1);

  // A key's body that never ends still gets its 403: nothing waited for the body.
  const port = new URL(base).port;
  const answer = await new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port, method: "POST", path: APPROVAL, headers: { ...KEY, "content-type": "application/json", "transfer-encoding": "chunked" } }, (response) => {
      response.resume();
      response.on("end", () => { request.destroy(); resolve(response.statusCode); });
    });
    request.on("error", (error) => (error.code === "ECONNRESET" ? undefined : reject(error)));
    request.write("{\"decision\":");
  });
  assert.equal(answer, 403);
  assert.equal(calls.upstream.length, 1);
});

test("X-1: a Future AGI API key cannot mint a conversation computer's channel token; a session can, through the gateway", async (t) => {
  const MINT = "/workspace/conversations/c-1/computer/channel";
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: { exchange: (init) => json({ accessToken: init.headers["x-api-key"] ? mintKeyAssertion() : mintAssertion() }) },
  });
  const mint = (headers) => fetch(`${base}${MINT}`, { method: "POST", headers });
  // By key (exchanged, then refused on its verified `api_key` claims): 403, and EE never sees it.
  const byKey = await mint(KEY);
  assert.equal(byKey.status, 403);
  assert.deepEqual({ ...(await byKey.json()), requestId: undefined }, { ...REFUSED, requestId: undefined });
  // A presented assertion that says api_key is refused the same way.
  const byKeyAssertion = await mint({ authorization: `Bearer ${mintKeyAssertion()}` });
  assert.equal(byKeyAssertion.status, 403);
  assert.equal(calls.upstream.length, 0, "no key mint reached EE");
  // The Console's path: the person's browser session, exchanged for a session assertion, is forwarded to EE.
  const bySession = await mint({ authorization: "Bearer fa-browser-token" });
  assert.equal(bySession.status, 200);
  assert.equal(calls.upstream.length, 1);
  assert.equal(claimsOf(calls.upstream[0].init.headers.authorization.slice("Bearer ".length)).auth_method, "session");
  // The decision reads the verified claims: a presented session assertion is forwarded too.
  assert.equal((await mint({ authorization: `Bearer ${mintAssertion()}` })).status, 200);
  assert.equal(calls.upstream.length, 2);
});

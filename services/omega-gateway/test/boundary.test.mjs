// Server-level boundary: what reaches the issuer and what reaches EE.
import test from "node:test";
import assert from "node:assert/strict";
import { loadConfig } from "../src/policy.mjs";
import { claimsOf, cloudEnv, DEPLOYMENT, foreignKey, json, mintAssertion, REMOTE, startGateway } from "./helpers.mjs";

const configFor = (t, extra = {}) => loadConfig(cloudEnv(t, extra));
const TRACE_KEYS = ["traceparent", "x-request-id"];
const withoutTrace = (headers) => Object.fromEntries(Object.entries(headers).filter(([name]) => !TRACE_KEYS.includes(name)));

test("operator and EE-only routes answer 403 before any issuer or EE call", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  for (const [method, path] of [
    ["GET", "/os/sandboxes"], ["POST", "/os/sandboxes"], ["GET", "/os/agents/a-1/deployments"], ["DELETE", "/os/agents/a-1"],
    ["GET", "/os/audit"], ["GET", "/os/memory/recall"], ["GET", "/auth/orgs"], ["GET", "/billing/plans"], ["GET", "/files"],
  ]) {
    for (const authorization of ["Bearer browser-session", `Bearer ${mintAssertion({ role: "owner" })}`]) {
      const response = await fetch(`${base}${path}`, { method, headers: { authorization } });
      assert.equal(response.status, 403, `${method} ${path}`);
      const body = await response.json();
      assert.equal(body.error, "route_not_allowed");
      assert.equal(body.requestId, response.headers.get("x-request-id"));
    }
  }
  assert.equal(calls.exchange.length, 0);
  assert.equal(calls.upstream.length, 0);
});

test("a pre-minted customer assertion is forwarded once, unchanged, without an exchange", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const assertion = mintAssertion({ role: "admin" });
  const response = await fetch(`${base}/workspace/sandboxes?limit=5`, {
    headers: {
      authorization: `Bearer ${assertion}`,
      "x-workspace-id": claimsOf(assertion).workspace_id,
      "x-organization-id": claimsOf(assertion).org_id,
      "x-omega-workspace": "spoofed",
      "x-omega-principal-roles": "operator",
      "x-forwarded-for": "10.0.0.1",
      cookie: "sessionid=host",
      "idempotency-key": "k-1",
      "if-match": "\"3\"",
    },
  });
  assert.equal(response.status, 200);
  assert.equal(calls.exchange.length, 0);
  assert.equal(calls.upstream.length, 1);
  assert.equal(calls.upstream[0].url, `${REMOTE}/workspace/sandboxes?limit=5`);
  assert.deepEqual(withoutTrace(calls.upstream[0].init.headers), {
    accept: "*/*", authorization: `Bearer ${assertion}`, "idempotency-key": "k-1", "if-match": "\"3\"",
  });
});

test("forged, expired, viewer and foreign-scope assertions never reach EE", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const now = Math.floor(Date.now() / 1000);
  const genuine = mintAssertion();
  for (const [token, status, error] of [
    [mintAssertion({ role: "owner" }, { privateKey: foreignKey.privateKey }), 401, "omega_assertion_rejected"],
    [mintAssertion({ iat: now - 60, exp: now - 15 }), 401, "omega_assertion_rejected"],
    [mintAssertion({ role: "viewer" }), 401, "omega_assertion_rejected"],
    [mintAssertion({ aud: "omega-operator" }), 401, "omega_assertion_rejected"],
  ]) {
    const response = await fetch(`${base}/workspace/deployments`, { headers: { authorization: `Bearer ${token}` } });
    assert.equal(response.status, status);
    assert.equal((await response.json()).error, error);
  }
  const mismatch = await fetch(`${base}/workspace/deployments`, {
    headers: { authorization: `Bearer ${genuine}`, "x-workspace-id": "44444444-4444-4444-8444-444444444444" },
  });
  assert.equal(mismatch.status, 403);
  assert.equal((await mismatch.json()).error, "omega_scope_mismatch");
  const anonymous = await fetch(`${base}/workspace/deployments`);
  assert.equal(anonymous.status, 401);
  assert.equal(calls.upstream.length, 0);
  assert.equal(calls.exchange.length, 0);
});

test("an issuer answer that is not a customer assertion is refused", async (t) => {
  for (const accessToken of ["opaque-operator-token", mintAssertion({ role: "viewer" }), mintAssertion({}, { privateKey: foreignKey.privateKey })]) {
    const { base, calls } = await startGateway(t, { config: configFor(t), handler: { exchange: () => json({ accessToken }) } });
    const response = await fetch(`${base}/workspace/capabilities`, { headers: { authorization: "Bearer browser-session" } });
    assert.equal(response.status, 401);
    assert.equal((await response.json()).error, "omega_session_unavailable");
    assert.equal(calls.exchange.length, 1);
    assert.equal(calls.upstream.length, 0);
  }
  // Not a token at all: the issuer is broken, not the session (Future AGI is unavailable).
  const { base, calls } = await startGateway(t, { config: configFor(t), handler: { exchange: () => json({ accessToken: "" }) } });
  const response = await fetch(`${base}/workspace/capabilities`, { headers: { authorization: "Bearer browser-session" } });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "omega_issuer_unavailable");
  assert.equal(calls.upstream.length, 0);
});

test("the browser token goes only to the issuer, never to EE; cookies go nowhere", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const response = await fetch(`${base}/workspace/sandboxes/sbx-1`, {
    headers: { authorization: "Bearer fi-browser-token", cookie: "sessionid=host", "x-workspace-id": "ws-selected" },
  });
  assert.equal(response.status, 200);
  assert.equal(calls.exchange[0].init.headers.authorization, "Bearer fi-browser-token");
  assert.equal(calls.exchange[0].init.headers["x-workspace-id"], "ws-selected");
  assert.equal(calls.exchange[0].init.headers.cookie, undefined);
  const forwarded = calls.upstream[0].init.headers;
  assert.match(forwarded.authorization, /^Bearer omega_delegate_/);
  assert.equal(JSON.stringify(forwarded).includes("fi-browser-token"), false);
  assert.equal(forwarded["x-workspace-id"], undefined);
  assert.equal(forwarded.cookie, undefined);
});

test("serves under a base path and refuses paths outside it", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t, { OMEGA_GATEWAY_BASE_PATH: "/omega/api/" }) });
  const ok = await fetch(`${base}/omega/api/workspace/deployments/${DEPLOYMENT}/health`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(ok.status, 200);
  assert.equal(calls.upstream[0].url, `${REMOTE}/workspace/deployments/${DEPLOYMENT}/health`);
  const outside = await fetch(`${base}/workspace/deployments`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(outside.status, 404);
  const sneaky = await fetch(`${base}/omega/apix/workspace/deployments`, { headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(sneaky.status, 404);
  assert.equal((await fetch(`${base}/omega/api/healthz`)).status, 200);
  assert.equal(calls.upstream.length, 1);
});

test("bundle routes get the bundle cap, JSON routes the JSON cap, and body-less routes forward nothing", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t, { OMEGA_MAX_BODY_BYTES: "1024", OMEGA_MAX_BUNDLE_BYTES: "2097152" }),
    handler: { upstream: (url, init) => json({ received: init.body ? init.body.length : 0 }, 202) },
  });
  const bundle = Buffer.alloc(1_500_000, 7);
  const upload = await fetch(`${base}/workspace/deployments`, {
    method: "POST", body: bundle, headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/x-tar" },
  });
  assert.equal(upload.status, 202);
  assert.deepEqual(await upload.json(), { received: 1_500_000 });
  assert.equal(calls.upstream[0].init.headers["content-type"], "application/x-tar");
  const tooBig = await fetch(`${base}/workspace/sandboxes`, {
    method: "POST", body: JSON.stringify({ pad: "x".repeat(4096) }), headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json" },
  });
  assert.equal(tooBig.status, 413);
  assert.equal((await tooBig.json()).error, "request_body_too_large");
  const stray = await fetch(`${base}/workspace/sandboxes/sbx-1`, { method: "DELETE", body: "{}", headers: { authorization: `Bearer ${mintAssertion()}` } });
  assert.equal(stray.status, 202);
  assert.equal(calls.upstream.at(-1).init.body, undefined);
  assert.equal(calls.upstream.length, 2);
});

test("exec streams pass through as SSE with receipt headers and no EE location", async (t) => {
  const { base } = await startGateway(t, {
    config: configFor(t),
    handler: {
      upstream: () => new Response(new ReadableStream({ start(controller) {
        controller.enqueue(new TextEncoder().encode("id: 1\nevent: stdout\ndata: {\"text\":\"hi\"}\n\n"));
        controller.close();
      } }), { headers: { "content-type": "text/event-stream", "x-request-id": "req-9", location: "https://omega.internal/x", etag: "\"7\"" } }),
    },
  });
  const response = await fetch(`${base}/workspace/sandboxes/sbx-1/exec`, {
    method: "POST", body: JSON.stringify({ command: ["echo", "hi"] }),
    headers: { authorization: `Bearer ${mintAssertion()}`, "content-type": "application/json", accept: "text/event-stream" },
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "text/event-stream");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.match(response.headers.get("x-request-id"), /^[0-9a-f]{32}$/);
  assert.equal(response.headers.get("x-upstream-request-id"), "req-9");
  assert.equal(response.headers.get("etag"), "\"7\"");
  assert.equal(response.headers.get("location"), null);
  assert.match(await response.text(), /event: stdout/);
});

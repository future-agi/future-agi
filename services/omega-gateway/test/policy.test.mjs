// Ported from Squad's services/omega-squad-gateway/test/policy.test.mjs
// (7debd6482). Adapted: CONTRACT.md closes the whole operator /os namespace,
// so every assertion that admitted a Squad /os/* projection now expects a
// refusal (41 flipped from true to false). The rebuild replaced the hand-written route
// lists with the route manifest: route assertions now live in wall.test.mjs and
// customer-routes.test.mjs; the config assertions here are verbatim.
import test from "node:test";
import assert from "node:assert/strict";
import { isAllowedPath, isAllowedRoute, loadConfig, normalizeRemoteBaseUrl, parseAllowedOrigins } from "../src/policy.mjs";
import { writeJwks } from "./helpers.mjs";

test("requires a remote HTTPS deployment", () => {
  assert.equal(normalizeRemoteBaseUrl("https://omega.example.com/") , "https://omega.example.com");
  assert.throws(() => normalizeRemoteBaseUrl("http://localhost:8317"));
  assert.throws(() => normalizeRemoteBaseUrl("https://omega.example.com/?token=secret"));
  assert.throws(() => normalizeRemoteBaseUrl("https://user:secret@omega.example.com"));
});

test("member, invite and billing writes and the org and billing reads are closed (CP P4)", () => {
  for (const [method, path] of [
    ["GET", "/auth/orgs"],
    ["GET", "/auth/orgs/org-1/members"],
    ["GET", "/auth/orgs/org-1/invites"],
    ["POST", "/auth/orgs/org-1/invites"],
    ["DELETE", "/auth/orgs/org-1/invites/invite-1"],
    ["POST", "/auth/orgs/org-1/invites/invite-1/reissue"],
    ["PATCH", "/auth/orgs/org-1/members/acct-2"],
    ["DELETE", "/auth/orgs/org-1/members/acct-2"],
    ["GET", "/billing/plans"],
    ["POST", "/billing/accounts/acct-1/checkout"],
    ["POST", "/billing/accounts/acct-1/subscription/cancel"],
    ["POST", "/billing/accounts/acct-1/credits"],
    ["POST", "/billing/accounts/acct-1/seats"],
    ["GET", "/files"],
    ["GET", "/files/file-1/content"],
  ]) {
    assert.equal(isAllowedRoute(method, path), false, `${method} ${path}`);
  }
  assert.equal(isAllowedPath("/os/agents"), false);
});

test("fails closed without a tenant-bound credential exchange", () => {
  assert.throws(() => loadConfig({ OMEGA_REMOTE_BASE_URL: "https://omega.example.com" }));
});

test("bounds gateway dependency timeouts", (t) => {
  const required = { OMEGA_ASSERTION_JWKS_FILE: writeJwks(t), OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379" };
  const defaults = loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    ...required,
  });
  assert.equal(defaults.requestTimeoutMs, 15_000);
  assert.equal(defaults.agentDraftTimeoutMs, 35_000);
  assert.equal(loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    OMEGA_AGENT_DRAFT_TIMEOUT_MS: "200000",
    ...required,
  }).agentDraftTimeoutMs, 120_000);
  assert.equal(loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    OMEGA_REQUEST_TIMEOUT_MS: "5000",
    ...required,
  }).requestTimeoutMs, 5_000);
  assert.equal(loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    OMEGA_REQUEST_TIMEOUT_MS: "10",
    ...required,
  }).requestTimeoutMs, 1_000);
  assert.equal(loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    OMEGA_REQUEST_TIMEOUT_MS: "invalid",
    ...required,
  }).requestTimeoutMs, 15_000);
});

test("rejects static operator credentials for the tenant gateway", () => {
  assert.throws(() => loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange",
    OMEGA_UPSTREAM_TOKEN: "operator-token",
  }), /not supported/);
  assert.throws(() => loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://localhost/session",
  }), /remote cloud deployment/);
  assert.throws(() => loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange?token=secret",
  }), /query parameters/);
  assert.throws(() => loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.com",
    OMEGA_SESSION_EXCHANGE_URL: "https://user:secret@futureagi.example.com/exchange",
  }), /credentials/);
});

test("requires HTTPS browser origins", () => {
  assert.deepEqual(parseAllowedOrigins("https://app.example.com"), ["https://app.example.com"]);
  assert.throws(() => parseAllowedOrigins("http://app.example.com"), /non-HTTPS/);
  assert.throws(() => parseAllowedOrigins("https://localhost"), /local origin/);
  assert.throws(() => parseAllowedOrigins("https://app.example.com/squad"), /without paths/);
});

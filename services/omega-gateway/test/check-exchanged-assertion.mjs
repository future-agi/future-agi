// Invoked by the Django exchange regression with its actual authenticated response.
// Only the exchange transport is substituted; signing, claims and gateway checks are real.
import assert from "node:assert/strict";
import { createPublicKey } from "node:crypto";
import { readFileSync } from "node:fs";
import { checkCustomerAssertion } from "../src/assertion.mjs";
import { checkPresentedAssertion, mintFromCredential, presentedCredential } from "../src/credential.mjs";
import { decideIdentity, decideRoute } from "../src/routes.mjs";

const input = JSON.parse(readFileSync(0, "utf8"));
const token = input.response.accessToken;
assert.ok(token.length <= 8192, "complete prefixed assertion exceeds transport limit");
const publicKeys = new Map([[input.kid, createPublicKey(input.publicKey)]]);
const config = {
  assertionIssuer: input.issuer,
  assertionPublicKeys: publicKeys,
  requestTimeoutMs: 1000,
  exchangeUrl: "http://127.0.0.1:9/accounts/omega/session-exchange/",
};
const verified = checkCustomerAssertion(token, { issuer: input.issuer, publicKeys });
assert.equal(verified.ok, true);
assert.equal(verified.claims.org_name, input.organizationName);
assert.equal(verified.claims.workspace_name, input.workspaceName);
assert.equal(verified.claims.role, "member");
assert.equal(verified.claims.emergency_control, "workspace");
const request = { headers: {
  authorization: `Bearer ${token}`,
  "x-organization-id": verified.claims.org_id,
  "x-workspace-id": verified.claims.workspace_id,
} };
assert.equal(presentedCredential(request).kind, "assertion");
assert.ok(checkPresentedAssertion(token, request, config).claims);
const minted = await mintFromCredential(
  { kind: "browser", token: "synthetic-browser-session" }, request, config,
  async () => new Response(JSON.stringify(input.response), { status: 200 }),
);
assert.ok(minted.claims, "gateway refused the Django-minted assertion");
assert.equal(minted.claims.org_name, input.organizationName);
assert.equal(minted.claims.workspace_name, input.workspaceName);
assert.equal(decideIdentity(decideRoute("GET", "/workspace/conversations"), minted.claims).allowed, true);
assert.equal(decideIdentity(decideRoute("POST", "/workspace/kill-switch/workspace"), minted.claims).allowed, true);
assert.equal(decideIdentity(decideRoute("POST", "/workspace/kill-switch/organization"), minted.claims).allowed, false);
// Retain the rejection boundary too: the repair must not expand gateway limits.
const oversized = token + "x".repeat(8193 - token.length);
assert.equal(checkCustomerAssertion(oversized).ok, false);
assert.equal(presentedCredential({ headers: { authorization: `Bearer ${oversized}` } }).status, 401);
console.log("accepted");

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { checkCustomerAssertion, parsePublicKeys } from "../src/assertion.mjs";
import { claimsOf, foreignKey, issuerKey, mintAssertion, writeJwks } from "./helpers.mjs";

const keysFor = (t, options) => parsePublicKeys(JSON.parse(fs.readFileSync(writeJwks(t, options), "utf8")));

test("accepts a fresh customer assertion for each customer role", () => {
  for (const role of ["owner", "admin", "member"]) {
    const token = mintAssertion({ role });
    const result = checkCustomerAssertion(token);
    assert.equal(result.ok, true, role);
    assert.equal(result.claims.role, role);
  }
});

test("refuses non-customer credentials and roles", () => {
  for (const [token, reason] of [
    ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "not_a_customer_assertion"],
    ["omega_live_operator_key", "not_a_customer_assertion"],
    ["omega_delegate_only.two", "malformed_assertion"],
    ["omega_delegate_a.b.c+d", "malformed_assertion"],
    [mintAssertion({ role: "viewer" }), "not_a_customer_role"],
    [mintAssertion({ role: "operator" }), "not_a_customer_role"],
    [mintAssertion({ aud: "omega-operator" }), "wrong_audience"],
    [mintAssertion({ workspace_id: "" }), "malformed_assertion"],
    [mintAssertion({ jti: "short" }), "malformed_assertion"],
    [mintAssertion({}, { header: { alg: "none" } }), "malformed_assertion"],
    [mintAssertion({}, { header: { alg: "HS256" } }), "malformed_assertion"],
    [mintAssertion({ auth_method: "unknown" }), "malformed_authentication_claim"],
    [mintAssertion({ auth_method: "session", sid: "bad id" }), "malformed_authentication_claim"],
    [mintAssertion({ auth_method: "session", key_id: "key-1", sid: "44444444-4444-4444-8444-444444444444" }), "malformed_authentication_claim"],
    [mintAssertion({ auth_method: "api_key", key_id: "key-1" }), "malformed_authentication_claim"],
  ]) {
    const result = checkCustomerAssertion(token);
    assert.deepEqual(result, { ok: false, reason }, token.slice(0, 40));
  }
});

test("enforces the 45 second lifetime and expiry", () => {
  const now = Math.floor(Date.now() / 1000);
  assert.equal(checkCustomerAssertion(mintAssertion({ iat: now - 46, exp: now - 1 })).reason, "expired_assertion");
  assert.equal(checkCustomerAssertion(mintAssertion({ iat: now, exp: now + 3600 })).reason, "invalid_lifetime");
  assert.equal(checkCustomerAssertion(mintAssertion({ iat: now + 120, exp: now + 160 })).reason, "invalid_lifetime");
  assert.equal(checkCustomerAssertion(mintAssertion({ iat: now, exp: now })).reason, "invalid_lifetime");
  const token = mintAssertion({ iat: now, exp: now + 45 });
  assert.equal(checkCustomerAssertion(token, { now: now + 44 }).ok, true);
  assert.equal(checkCustomerAssertion(token, { now: now + 45 }).reason, "expired_assertion");
});

test("verifies signatures and issuer when configured", (t) => {
  const keys = keysFor(t);
  const genuine = mintAssertion();
  assert.equal(checkCustomerAssertion(genuine, { publicKeys: keys, issuer: claimsOf(genuine).iss }).ok, true);
  assert.equal(checkCustomerAssertion(genuine, { issuer: "https://other.example.test" }).reason, "wrong_issuer");
  assert.equal(checkCustomerAssertion(mintAssertion({}, { privateKey: foreignKey.privateKey }), { publicKeys: keys }).reason, "bad_signature");
  assert.equal(checkCustomerAssertion(mintAssertion({}, { kid: "rotated-away" }), { publicKeys: keys }).reason, "unknown_key");
  // A role upgrade on a genuine token breaks its signature.
  const [prefixAndHeader, , signature] = genuine.split(".");
  const promoted = Buffer.from(JSON.stringify({ ...claimsOf(genuine), role: "owner" })).toString("base64url");
  assert.equal(checkCustomerAssertion(`${prefixAndHeader}.${promoted}.${signature}`, { publicKeys: keys }).reason, "bad_signature");
});

test("JWKS loading refuses private material and wrong algorithms", (t) => {
  const privateJwk = issuerKey.privateKey.export({ format: "jwk" });
  assert.throws(() => parsePublicKeys({ keys: [{ ...privateJwk, kid: "k" }] }), /public keys only/);
  assert.throws(() => keysFor(t, { extra: { alg: "RS512" } }), /RS256/);
  assert.throws(() => parsePublicKeys({ keys: [] }), /no keys/);
});

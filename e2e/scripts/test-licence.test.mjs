import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createPublicKey, verify } from "node:crypto";
import { mkdtempSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { licenceClaims, writeLicenceEnv } from "./test-licence.mjs";

const SCRIPT = fileURLToPath(new URL("./test-licence.mjs", import.meta.url));

const readEnv = (file) =>
  Object.fromEntries(
    readFileSync(file, "utf8")
      .split("\n")
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]),
  );

const decode = (part) => JSON.parse(Buffer.from(part, "base64url").toString("utf8"));

function publicKeyOf(env) {
  return createPublicKey(env.EE_LICENSE_PUBLIC_KEY.replace(/\\n/g, "\n"));
}

test("enterprise: an RS256 licence the env public key verifies, with the validator's fixed claims", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-licence-"));
  const env = readEnv(writeLicenceEnv(dir, "enterprise"));
  const [header, payload, signature] = env.EE_LICENSE_KEY.split(".");
  assert.deepEqual(decode(header), { alg: "RS256", typ: "JWT", kid: "default" });
  assert.equal(
    verify("sha256", Buffer.from(`${header}.${payload}`), publicKeyOf(env), Buffer.from(signature, "base64url")),
    true,
  );
  const claims = decode(payload);
  assert.equal(claims.iss, "https://licenses.futureagi.com");
  assert.equal(claims.aud, "futureagi-self-hosted");
  assert.equal(claims.typ, "futureagi-enterprise-license");
  assert.equal(claims.schema_version, 1);
  assert.equal(claims.license_type, "production");
  assert.deepEqual(claims.features, []);
  assert.ok(claims.exp > Date.now() / 1000 + 300 * 86_400);
  assert.equal(env.E2E_LICENCE_STATE, "enterprise");
});

test("expired: signed by the same key, expired well past the 300 s clock skew", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-licence-"));
  const first = readEnv(writeLicenceEnv(dir, "enterprise"));
  const env = readEnv(writeLicenceEnv(dir, "expired"));
  assert.equal(env.EE_LICENSE_PUBLIC_KEY, first.EE_LICENSE_PUBLIC_KEY);
  const claims = decode(env.EE_LICENSE_KEY.split(".")[1]);
  assert.ok(claims.exp < Date.now() / 1000 - 300);
  assert.ok(claims.iat <= claims.exp && claims.nbf <= claims.exp);
});

test("removed: no licence key, the trusted public key stays", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-licence-"));
  const env = readEnv(writeLicenceEnv(dir, "removed"));
  assert.equal(env.EE_LICENSE_KEY, "");
  assert.match(env.EE_LICENSE_PUBLIC_KEY, /^-----BEGIN PUBLIC KEY-----\\n/);
});

test("key material is private to the user and never printed", () => {
  const dir = path.join(mkdtempSync(path.join(tmpdir(), "e2e-licence-")), "stack");
  const run = spawnSync(process.execPath, [SCRIPT, "write-env", dir, "enterprise"], { encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.stdout, "test licence: enterprise\n");
  assert.equal(run.stderr, "");
  assert.equal(statSync(dir).mode & 0o777, 0o700);
  for (const file of ["signing-key.pem", "public-key.pem", "licence.env"]) {
    assert.equal(statSync(path.join(dir, file)).mode & 0o777, 0o600, file);
  }
});

test("an unknown state is refused", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-licence-"));
  assert.throws(() => writeLicenceEnv(dir, "trial"), /licence state must be one of/);
  const run = spawnSync(process.execPath, [SCRIPT, "write-env", dir, "grace"], { encoding: "utf8" });
  assert.equal(run.status, 1);
});

test("claims are deterministic for a fixed clock", () => {
  assert.deepEqual(licenceClaims("enterprise", 1_000_000_000), licenceClaims("enterprise", 1_000_000_000));
});

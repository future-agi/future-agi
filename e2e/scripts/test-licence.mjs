#!/usr/bin/env node
// Test-signed Enterprise licences for the E2E stacks (e2e/README.md,
// "Licences on the E2E stacks"). Node built-ins only: bin/e2e runs this
// before `yarn install` has happened.
//
//   node e2e/scripts/test-licence.mjs write-env <dir> <enterprise|expired|removed>
//
// Keeps one throwaway RSA keypair per stack in <dir> (0700; files 0600) and
// writes <dir>/licence.env for `docker compose --env-file`:
//
//   EE_LICENSE_KEY         a licence signed with that key (empty for `removed`)
//   EE_LICENSE_PUBLIC_KEY  the key the backend trusts, through the keyring's
//                          pre-GA env path (futureagi/ee/licensing/keyring.py)
//
// The backend's own validator decides what the licence is worth; nothing here
// grants anything by itself. Once a production key is bundled in
// keyring._BUNDLED_KEYS the env key is ignored, every licence written here
// validates as invalid and the stack runs as Community (fail closed).
//
// Never a production key, and nothing derived from either key is printed.
import { generateKeyPairSync, createPrivateKey, sign } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

// futureagi/ee/licensing/keyring.py REQUIRED_ISSUER / _AUDIENCE / _TYPE, and
// validator.py REQUIRED_SCHEMA_VERSION.
const ISSUER = "https://licenses.futureagi.com";
const AUDIENCE = "futureagi-self-hosted";
const TYPE = "futureagi-enterprise-license";
const SCHEMA_VERSION = 1;
// keyring._parse_env_keys: EE_LICENSE_PUBLIC_KEY is kid "default", RS256.
const KID = "default";
const DAY = 86_400;
// Past the validator's default clock skew (EE_LICENSE_CLOCK_SKEW_SECONDS, 300 s).
const EXPIRED_DAYS_AGO = 10;

export const STATES = Object.freeze(["enterprise", "expired", "removed"]);

const base64url = (value) =>
  Buffer.from(value)
    .toString("base64")
    .replace(/=+$/, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");

/** Claims shaped like ee/licensing/tests/fixtures.py license_claims. */
export function licenceClaims(state, now = Math.floor(Date.now() / 1000)) {
  const exp =
    state === "expired" ? now - EXPIRED_DAYS_AGO * DAY : now + 365 * DAY;
  const issued = exp - 30 * DAY;
  return {
    typ: TYPE,
    schema_version: SCHEMA_VERSION,
    license_id: "lic_e2e_test_0001",
    customer_id: "cus_e2e_test",
    issued_to: "E2E Test Licence",
    iss: ISSUER,
    aud: AUDIENCE,
    iat: Math.min(now, issued),
    nbf: Math.min(now, issued),
    exp,
    license_type: "production",
    band: "e2e-test",
    // No product features: the licence lifts the Community rule (any usable
    // licence does) without switching on Falcon AI or other managed-network
    // products, so a test stack never needs Future AGI's services.
    features: [],
    limits: {},
    max_instances: 1,
    grace_days: 0,
  };
}

export function signLicence(privateKeyPem, claims) {
  const header = { alg: "RS256", typ: "JWT", kid: KID };
  const input = `${base64url(JSON.stringify(header))}.${base64url(JSON.stringify(claims))}`;
  const signature = sign(
    "sha256",
    Buffer.from(input),
    createPrivateKey(privateKeyPem),
  );
  return `${input}.${base64url(signature)}`;
}

function writePrivate(file, contents) {
  const tmp = `${file}.tmp-${process.pid}`;
  writeFileSync(tmp, contents, { mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, file);
}

/** The stack's keypair, created on first use. */
export function ensureKeypair(dir) {
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
  const privateFile = path.join(dir, "signing-key.pem");
  const publicFile = path.join(dir, "public-key.pem");
  if (!existsSync(privateFile) || !existsSync(publicFile)) {
    const { privateKey, publicKey } = generateKeyPairSync("rsa", {
      modulusLength: 2048,
      publicKeyEncoding: { type: "spki", format: "pem" },
      privateKeyEncoding: { type: "pkcs8", format: "pem" },
    });
    writePrivate(privateFile, privateKey);
    writePrivate(publicFile, publicKey);
  }
  return {
    privateKey: readFileSync(privateFile, "utf8"),
    publicKey: readFileSync(publicFile, "utf8"),
  };
}

/** Write <dir>/licence.env for `state`; returns its path. */
export function writeLicenceEnv(dir, state, now) {
  if (!STATES.includes(state)) {
    throw new Error(`licence state must be one of ${STATES.join(", ")}`);
  }
  const { privateKey, publicKey } = ensureKeypair(dir);
  const token =
    state === "removed"
      ? ""
      : signLicence(privateKey, licenceClaims(state, now));
  const envFile = path.join(dir, "licence.env");
  writePrivate(
    envFile,
    [
      `# Written by e2e/scripts/test-licence.mjs (${state}). Test-only; never commit.`,
      `E2E_LICENCE_STATE=${state}`,
      `EE_LICENSE_KEY=${token}`,
      // One line: tfc/settings/settings.py turns the literal \n back into newlines.
      `EE_LICENSE_PUBLIC_KEY=${publicKey.trim().replace(/\n/g, "\\n")}`,
      "",
    ].join("\n"),
  );
  return envFile;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [command, dir, state] = process.argv.slice(2);
  if (command !== "write-env" || !dir || !state) {
    console.error(
      `usage: test-licence.mjs write-env <dir> <${STATES.join("|")}>`,
    );
    process.exit(2);
  }
  try {
    writeLicenceEnv(path.resolve(dir), state);
    console.log(`test licence: ${state}`);
  } catch (err) {
    console.error(`test-licence: ${err.message}`);
    process.exit(1);
  }
}

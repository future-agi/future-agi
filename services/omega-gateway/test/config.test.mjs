import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig } from "../src/policy.mjs";
import { writeJwks } from "./helpers.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

function parseEnvFile(file) {
  const env = {};
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const match = /^([A-Z_]+)=(.*)$/.exec(line.trim());
    if (match) env[match[1]] = match[2];
  }
  return env;
}

test("local mode admits only loopback services and origins and binds loopback", () => {
  const config = loadConfig({
    OMEGA_GATEWAY_MODE: "local",
    OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318",
    OMEGA_SESSION_EXCHANGE_URL: "http://localhost:8000/accounts/omega/session-exchange/",
    OMEGA_ALLOWED_ORIGINS: "http://localhost:3031",
    HOST: "0.0.0.0",
  });
  assert.equal(config.mode, "local");
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.remoteBaseUrl, "http://127.0.0.1:4318");
  assert.deepEqual(config.allowedOrigins, ["http://localhost:3031"]);
  const local = { OMEGA_GATEWAY_MODE: "local", OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318", OMEGA_SESSION_EXCHANGE_URL: "http://127.0.0.1:8000/x/" };
  assert.throws(() => loadConfig({ ...local, OMEGA_REMOTE_BASE_URL: "https://omega.example.com" }), /loopback/);
  assert.throws(() => loadConfig({ ...local, OMEGA_REMOTE_BASE_URL: "http://0.0.0.0:4318" }), /loopback/);
  assert.throws(() => loadConfig({ ...local, OMEGA_SESSION_EXCHANGE_URL: "https://app.futureagi.com/accounts/omega/session-exchange/" }), /loopback/);
  assert.throws(() => loadConfig({ ...local, OMEGA_ALLOWED_ORIGINS: "https://app.futureagi.com" }), /loopback/);
  assert.throws(() => loadConfig({ ...local, OMEGA_REMOTE_BASE_URL: "ftp://127.0.0.1/" }), /http or https/);
  assert.throws(() => loadConfig({ ...local, OMEGA_UPSTREAM_TOKEN: "operator" }), /not supported/);
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_MODE: "dev" }), /cloud or local/);
});

test("cloud mode keeps refusing loopback and plain HTTP", (t) => {
  const cloud = { OMEGA_REMOTE_BASE_URL: "https://omega.example.com", OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange", OMEGA_ASSERTION_JWKS_FILE: writeJwks(t), OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "rediss://user:pw@redis.example.com:6380/2" };
  assert.equal(loadConfig(cloud).mode, "cloud");
  assert.equal(loadConfig(cloud).host, "0.0.0.0");
  assert.throws(() => loadConfig({ ...cloud, OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318" }), /https/);
  assert.throws(() => loadConfig({ ...cloud, OMEGA_ALLOWED_ORIGINS: "http://localhost:3031" }), /non-HTTPS/);
});

test("base path is normalised and validated", (t) => {
  const base = { OMEGA_REMOTE_BASE_URL: "https://omega.example.com", OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange", OMEGA_ASSERTION_JWKS_FILE: writeJwks(t), OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.com:6379" };
  assert.equal(loadConfig({ ...base, OMEGA_GATEWAY_BASE_PATH: "/omega/api/" }).basePath, "/omega/api");
  assert.equal(loadConfig(base).basePath, "");
  assert.throws(() => loadConfig({ ...base, OMEGA_GATEWAY_BASE_PATH: "omega/api" }), /absolute path/);
  assert.throws(() => loadConfig({ ...base, OMEGA_GATEWAY_BASE_PATH: "/omega/../os" }), /absolute path/);
});

test("the local config template is loopback-only and carries no credential", () => {
  const file = path.join(here, "..", "config", "local.env.example");
  const env = parseEnvFile(file);
  const config = loadConfig({ ...env, OMEGA_ASSERTION_JWKS_FILE: "" });
  assert.equal(config.mode, "local");
  assert.equal(config.basePath, "/omega/api");
  assert.deepEqual(config.allowedOrigins, ["http://localhost:3031"]);
  const text = fs.readFileSync(file, "utf8");
  assert.doesNotMatch(text, /BEGIN [A-Z ]*PRIVATE KEY|omega_delegate_[A-Za-z0-9]|OMEGA_UPSTREAM_TOKEN=|Bearer /);
});

test("cloud mode needs signature checks and a shared rate-limit store; local mode does not", (t) => {
  const cloud = { OMEGA_REMOTE_BASE_URL: "https://omega.example.com", OMEGA_SESSION_EXCHANGE_URL: "https://futureagi.example.com/exchange" };
  const jwks = writeJwks(t);
  assert.throws(() => loadConfig({ ...cloud, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://r.example.com" }), /OMEGA_ASSERTION_JWKS_FILE is required/);
  assert.throws(() => loadConfig({ ...cloud, OMEGA_ASSERTION_JWKS_FILE: jwks }), /OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL is required/);
  const store = loadConfig({ ...cloud, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "rediss://gw:s%40cret@redis.example.com:6380/3" }).rateLimitStore;
  assert.deepEqual(store, { kind: "redis", tls: true, host: "redis.example.com", port: 6380, username: "gw", password: "s@cret", db: 3 });
  for (const bad of ["http://redis.example.com", "redis://r.example.com/99", "redis://r.example.com/?x=1"]) {
    assert.throws(() => loadConfig({ ...cloud, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: bad }), /OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL/);
  }
  const local = { OMEGA_GATEWAY_MODE: "local", OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318", OMEGA_SESSION_EXCHANGE_URL: "http://127.0.0.1:8000/x/" };
  assert.equal(loadConfig(local).rateLimitStore.kind, "memory");
  assert.equal(loadConfig({ ...local, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://127.0.0.1:6379" }).rateLimitStore.kind, "redis");
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.com" }), /loopback/);
});

test("drain, readiness, public share, OTLP and proxy settings are bounded and default closed", (t) => {
  const local = { OMEGA_GATEWAY_MODE: "local", OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318", OMEGA_SESSION_EXCHANGE_URL: "http://127.0.0.1:8000/x/" };
  const defaults = loadConfig(local);
  assert.equal(defaults.drainMs, 10_000);
  assert.equal(defaults.publicShare, false);
  assert.equal(defaults.eeReadyPath, "/readyz");
  assert.equal(defaults.trustedProxyHops, 0);
  assert.equal(defaults.otlp, null);
  assert.equal(defaults.consoleOrigin, null);
  assert.deepEqual(defaults.timeouts, { read: 15_000, write: 30_000, model: 35_000, stream: 15_000 });
  assert.equal(loadConfig({ ...local, OMEGA_GATEWAY_DRAIN_MS: "90000" }).drainMs, 30_000);
  assert.equal(loadConfig({ ...local, OMEGA_GATEWAY_PUBLIC_SHARE: "on" }).publicShare, true);
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_PUBLIC_SHARE: "maybe" }), /on or off/);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "omega-gw-otlp-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "otlp.json");
  fs.writeFileSync(file, JSON.stringify({ apiKey: "k", secretKey: "s" }), { mode: 0o600 });
  const otlp = loadConfig({ ...local, OMEGA_GATEWAY_OTLP_TRACES_URL: "http://127.0.0.1:4318/v1/traces", OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE: file }).otlp;
  assert.deepEqual(otlp, { url: "http://127.0.0.1:4318/v1/traces", credentials: { apiKey: "k", secretKey: "s" }, projectName: "omega-console-ops", serviceName: "omega-gateway" });
  fs.writeFileSync(file, JSON.stringify({ apiKey: "k" }));
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_OTLP_TRACES_URL: "http://127.0.0.1:4318/v1/traces", OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE: file }), /apiKey/);
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_OTLP_TRACES_URL: "https://collector.example.com/v1/traces" }), /loopback/);
});

test("the Console origin must differ from the Future AGI origin and needs signature checks", (t) => {
  const local = { OMEGA_GATEWAY_MODE: "local", OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318", OMEGA_SESSION_EXCHANGE_URL: "http://127.0.0.1:8000/x/", OMEGA_ALLOWED_ORIGINS: "http://localhost:3031", OMEGA_GATEWAY_BASE_PATH: "/omega/api" };
  const jwks = writeJwks(t);
  assert.throws(() => loadConfig({ ...local, OMEGA_GATEWAY_BASE_PATH: "", OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032" }), /requires OMEGA_GATEWAY_BASE_PATH/);
  const ok = loadConfig({ ...local, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032", OMEGA_CONSOLE_STATIC_DIR: "/srv/console" }).consoleOrigin;
  assert.deepEqual(ok, { origin: "http://127.0.0.1:3032", frameAncestors: ["http://localhost:3031"], staticDir: "/srv/console", trustedTypes: "report", host: "127.0.0.1", port: 8081 });
  assert.throws(() => loadConfig({ ...local, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032" }), /needs OMEGA_ASSERTION_JWKS_FILE/);
  assert.throws(() => loadConfig({ ...local, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://localhost:3031" }), /must differ/);
  assert.throws(() => loadConfig({ ...local, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032", OMEGA_CONSOLE_STATIC_DIR: "relative/dir" }), /absolute/);
  assert.throws(() => loadConfig({ ...local, OMEGA_ALLOWED_ORIGINS: "", OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032" }), /FRAME_ANCESTORS/);
  assert.throws(() => loadConfig({ ...local, OMEGA_ASSERTION_JWKS_FILE: jwks, OMEGA_CONSOLE_ORIGIN: "http://127.0.0.1:3032", OMEGA_CONSOLE_TRUSTED_TYPES: "off" }), /report or enforce/);
});

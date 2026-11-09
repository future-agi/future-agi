// Test helpers: mint assertions in the exact wire format of
// futureagi/accounts/omega_session.py HostAssertionSigner.issue().
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const issuerKey = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
export const foreignKey = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });

const segment = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");

// What Django's exchange puts in a key for a workspace admitted to every hosted product (READINESS
// 3.13). Tests of the product wall override `products` to admit less.
export const ALL_PRODUCTS_ACTIVE = Object.freeze({
  console: "active", deployments: "active", hub: "active", sandboxes: "active", studio_tab: "active",
});

export function mintAssertion(overrides = {}, { privateKey = issuerKey.privateKey, kid = "test-key", header = {} } = {}) {
  const iat = Math.floor(Date.now() / 1000);
  const claims = {
    iss: "https://host.example.test/omega",
    aud: "omega-squad",
    sub: "11111111-1111-4111-8111-111111111111",
    org_id: "22222222-2222-4222-8222-222222222222",
    workspace_id: "33333333-3333-4333-8333-333333333333",
    role: "member",
    auth_method: "session",
    sid: "44444444-4444-4444-8444-444444444444",
    iat,
    exp: iat + 45,
    jti: crypto.randomBytes(32).toString("base64url"),
    nonce: crypto.randomBytes(32).toString("base64url"),
    products: ALL_PRODUCTS_ACTIVE,
    rollout_epoch: 1,
    ...overrides,
  };
  const signingInput = `${segment({ alg: "RS256", typ: "JWT", kid, ...header })}.${segment(claims)}`;
  const signature = crypto.sign("RSA-SHA256", Buffer.from(signingInput, "ascii"), privateKey).toString("base64url");
  return `omega_delegate_${signingInput}.${signature}`;
}

/** What Django mints for a personal API key (SB W2-KEYS): `auth_method` api_key, bound by `key_id`. */
export function mintKeyAssertion(overrides = {}, options = {}) {
  return mintAssertion({ auth_method: "api_key", sid: undefined, key_id: "55555555-5555-4555-8555-555555555555", ...overrides }, options);
}

export function claimsOf(token) {
  return JSON.parse(Buffer.from(token.slice("omega_delegate_".length).split(".")[1], "base64url").toString("utf8"));
}

/** A JWKS file for a whole test module (removed when the process exits), for module-level configs. */
export function suiteJwksFile({ publicKey = issuerKey.publicKey, kid = "test-key" } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "omega-gateway-jwks-"));
  process.once("exit", () => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "jwks.json");
  fs.writeFileSync(file, JSON.stringify({ keys: [{ ...publicKey.export({ format: "jwk" }), kid, alg: "RS256", use: "sig" }] }));
  return file;
}

export function writeJwks(t, { publicKey = issuerKey.publicKey, kid = "test-key", extra = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "omega-gateway-jwks-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "jwks.json");
  const jwk = publicKey.export({ format: "jwk" });
  fs.writeFileSync(file, JSON.stringify({ keys: [{ ...jwk, kid, alg: "RS256", use: "sig", ...extra }] }));
  return file;
}

export const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json", ...headers },
});

// Gateway harness shared by the server-level tests.
export const EXCHANGE = "https://futureagi.example.test/accounts/omega/session-exchange/";
export const REMOTE = "https://omega.example.test";
export const DEPLOYMENT = "dep-0123456789abcdef";

/** A valid cloud-mode environment: the JWKS and the shared rate-limit store are required there. */
export function cloudEnv(t, extra = {}) {
  return {
    OMEGA_REMOTE_BASE_URL: REMOTE,
    OMEGA_SESSION_EXCHANGE_URL: EXCHANGE,
    OMEGA_ASSERTION_JWKS_FILE: writeJwks(t),
    OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379",
    ...extra,
  };
}

export const quiet = () => {};

/**
 * Start one gateway on loopback with a recording upstream. `handler.exchange(init)` and
 * `handler.upstream(url, init)` answer the issuer and EE; both default to success.
 */
/**
 * The real route -> product table plus `extra` entries, for tests whose own manifest adds a route
 * the table does not cover (every manifest route must name a product, READINESS 3.13).
 */
export async function routeProductsWith(extra) {
  const fs = await import("node:fs");
  const { compileRouteProducts, ROUTE_PRODUCTS_PATH } = await import("../src/product-wall.mjs");
  const raw = JSON.parse(fs.readFileSync(ROUTE_PRODUCTS_PATH, "utf8"));
  return compileRouteProducts({ ...raw, routes: [...raw.routes, ...extra] });
}

export async function startGateway(t, { config, handler = {}, rateLimiter, log = quiet, manifest, exportFetch, exporter, routeProducts, refusalLog } = {}) {
  const { createGateway } = await import("../src/server.mjs");
  const { createRateLimiter } = await import("../src/ratelimit.mjs");
  const calls = { exchange: [], upstream: [], probes: [] };
  const gateway = createGateway({
    config,
    rateLimiter: rateLimiter ?? createRateLimiter(),
    log,
    manifest,
    exportFetch,
    exporter,
    ...(routeProducts ? { routeProducts } : {}),
    ...(refusalLog ? { refusalLog } : {}),
    fetchImpl: async (url, init) => {
      if (url === config.exchangeUrl && init?.method === "POST") {
        calls.exchange.push({ url, init, at: Date.now() });
        return handler.exchange ? handler.exchange(init) : json({ accessToken: mintAssertion() });
      }
      if (init?.method === "GET" && (url === config.exchangeUrl || url === `${config.remoteBaseUrl}${config.eeReadyPath}`)) {
        calls.probes.push(url);
        return handler.probe ? handler.probe(url) : json({ ok: true }, url === config.exchangeUrl ? 405 : 200);
      }
      calls.upstream.push({ url, init, at: Date.now() });
      return handler.upstream ? handler.upstream(url, init) : json({ ok: true });
    },
  });
  const servers = [gateway.app, gateway.console].filter(Boolean);
  for (const server of servers) await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    for (const server of servers) {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(() => resolve()));
    }
    gateway.rateLimiter.close();
    await gateway.exporter.shutdown?.();
  });
  const url = (server) => `http://127.0.0.1:${server.address().port}`;
  return { base: url(gateway.app), consoleBase: gateway.console ? url(gateway.console) : null, calls, gateway };
}

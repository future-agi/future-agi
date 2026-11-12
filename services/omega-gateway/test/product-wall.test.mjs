// The product wall (READINESS 3.13, RC-02 and RC-03 at the gateway): only a verified key that names
// the route's product reaches EE; paused products keep reads and refuse new work with 503; a route
// with no product is refused (default deny); nothing a browser can edit or replay gets past it.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { createGatewayServer } from "../src/server.mjs";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { isAllowedRoute } from "../src/routes.mjs";
import { MANIFEST_PATH } from "../src/manifest.mjs";
import {
  compileRouteProducts,
  decideProduct,
  loadRouteProducts,
  productsForPath,
} from "../src/product-wall.mjs";
import {
  ALL_PRODUCTS_ACTIVE,
  json,
  mintAssertion,
  suiteJwksFile,
} from "./helpers.mjs";

const TABLE = loadRouteProducts();
const VERIFIED = { verified: true };
const EXCHANGE =
  "https://futureagi.example.test/accounts/omega/session-exchange/";
const LOCAL_EXCHANGE = "http://127.0.0.1:9/accounts/omega/session-exchange/";
const JWKS = suiteJwksFile();

// One sample per route family the product table names, with its product. The route manifest (lane
// 1.3) is the gateway's allow-list; it closes /auth, /billing and /files (eeOnly) before this wall.
const FAMILIES = [
  ["GET", "/workspace/capabilities", "platform"],
  ["GET", "/workspace/sandboxes", "sandboxes"],
  ["POST", "/workspace/sandboxes/sb-1/exec", "sandboxes"],
  ["GET", "/workspace/deployments/dep-1/logs", "deployments"],
  ["POST", "/workspace/deployments", "deployments"],
  ["GET", "/workspace/credentials", "console,deployments"],
  ["GET", "/workspace/audit/integrity", "console"],
  ["GET", "/workspace/conversations/c-1/events", "console"],
  // P07 CHAT-TRACE (item 53 (d)): Settings > Traces in Observe, the Console's own settings.
  ["GET", "/workspace/observe/settings", "console"],
  ["PUT", "/workspace/observe/settings", "console"],
  ["POST", "/workspace/conversations", "console"],
  ["GET", "/workspace/workforce", "console"],
  ["GET", "/workspace/agent-teams", "console"],
  ["GET", "/workspace/skills", "console"],
  ["POST", "/workspace/approvals/a-1", "console"],
  ["GET", "/workspace/environments", "console"],
  ["GET", "/workspace/schedules", "console"],
  ["GET", "/workspace/ratings/summary", "console"],
  ["GET", "/workspace/integrations", "console"],
  ["GET", "/workspace/usage", "console"],
  // The Console's model connection status; the one-key prefix names nothing else the gateway admits.
  ["GET", "/workspace/one-key", "console"],
  ["GET", "/auth/orgs", "console"],
  ["GET", "/billing/plans", "console"],
  ["GET", "/files", "console"],
  // Integration round 4: the Console's chat attachments (CONSOLE-CHAT) and the agent computers the Console's chat and
  // the Sandboxes tab both show (SB-AGENT-COMPUTER-CORE; the longest prefix wins over /workspace/sandboxes).
  ["POST", "/workspace/files", "console"],
  ["GET", "/workspace/sandboxes/computers", "console,sandboxes"],
  [
    "GET",
    "/workspace/sandboxes/computers/cmp_0123456789abcdef01234567",
    "console,sandboxes",
  ],
];

test("every route family the allow-list admits names exactly its product", () => {
  for (const [method, path, product] of FAMILIES) {
    assert.equal(productsForPath(TABLE, path)?.join(","), product, path);
  }
  // Every route the manifest admits names a product (the merge of lanes 1.3 and 1.10): a manifest
  // route this table does not cover would be refused 403 no_product for every caller.
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  assert.ok(manifest.routes.length > 100);
  for (const route of manifest.routes) {
    const sample = route.path.replace(/\{[^}]+\}/g, "x1");
    assert.notEqual(
      productsForPath(TABLE, sample),
      undefined,
      `${route.method} ${route.path} names no product`,
    );
  }
  for (const [method, path] of [
    ["GET", "/workspace/sandboxes"],
    ["POST", "/workspace/deployments"],
    ["GET", "/workspace/capabilities"],
  ]) {
    assert.equal(
      isAllowedRoute(method, path),
      true,
      `${method} ${path} is admitted by the manifest`,
    );
  }
  // A segment boundary, not a string prefix: /workspace/sandboxesX is not the sandboxes family.
  assert.equal(productsForPath(TABLE, "/workspace/sandboxesx"), undefined);
  assert.equal(productsForPath(TABLE, "/workspace/unknown"), undefined);
  assert.equal(productsForPath(TABLE, "/workspace/%73andboxes"), undefined);
  assert.equal(
    productsForPath(TABLE, "/workspace/sandboxes/%E0%A4%A"),
    undefined,
  );
  assert.equal(
    productsForPath(TABLE, "/workspace/sandboxes/my%20file.txt")?.join(),
    "sandboxes",
  );
});

test("emergency stop remains available while Console work is paused, but resume does not", () => {
  const claims = { products: { console: "paused" }, rollout_epoch: 1 };
  assert.equal(
    productsForPath(TABLE, "/workspace/kill-switch/status")?.join(","),
    "console",
  );
  for (const scope of ["workspace", "organization"]) {
    assert.equal(
      decideProduct(
        TABLE,
        "POST",
        `/workspace/kill-switch/${scope}`,
        claims,
        VERIFIED,
      ).ok,
      true,
    );
    assert.equal(
      decideProduct(
        TABLE,
        "POST",
        `/workspace/kill-switch/${scope}/resume`,
        claims,
        VERIFIED,
      )?.status,
      503,
    );
  }
});

test("the table is strict: a typo fails the load instead of widening the wall", () => {
  const good = JSON.parse(
    JSON.stringify({
      schemaVersion: "omega-route-products/v1",
      about: "",
      products: ["console"],
      routes: [{ prefix: "/workspace/x", product: "console" }],
      whilePaused: [],
    }),
  );
  assert.doesNotThrow(() => compileRouteProducts(good));
  const broken = [
    { ...good, extra: 1 },
    { ...good, schemaVersion: "v2" },
    { ...good, products: ["console", "platform"] },
    { ...good, routes: [{ prefix: "/workspace/x", product: "consle" }] },
    { ...good, routes: [{ prefix: "/workspace/x", product: [] }] },
    {
      ...good,
      routes: [{ prefix: "/workspace/x", product: ["console", "platform"] }],
    },
    {
      ...good,
      routes: [
        { prefix: "/workspace/x", product: "console" },
        { prefix: "/workspace/x", product: "console" },
      ],
    },
    { ...good, routes: [{ prefix: "/workspace/x/", product: "console" }] },
    {
      ...good,
      routes: [{ prefix: "/workspace/x", product: "console", method: "GET" }],
    },
    { ...good, whilePaused: [{ method: "GET", path: "/workspace/x" }] },
  ];
  for (const input of broken) assert.throws(() => compileRouteProducts(input));
});

test("the decision: unverified, absent, other product, platform, shared and paused", () => {
  const claims = (products) => ({ products });
  assert.deepEqual(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/sandboxes",
      claims(ALL_PRODUCTS_ACTIVE),
      { verified: false },
    ),
    {
      status: 503,
      error: "verification_unavailable",
      reason: "unverified_claims",
    },
  );
  assert.equal(
    decideProduct(TABLE, "GET", "/workspace/sandboxes", {}, VERIFIED).error,
    "product_not_admitted",
  );
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/sandboxes",
      claims({ sandboxes: "on" }),
      VERIFIED,
    ).error,
    "product_not_admitted",
  );
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/sandboxes",
      claims({ console: "active" }),
      VERIFIED,
    ).status,
    404,
  );
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/capabilities",
      claims({ hub: "paused" }),
      VERIFIED,
    ).ok,
    true,
  );
  assert.equal(
    decideProduct(TABLE, "GET", "/workspace/capabilities", claims({}), VERIFIED)
      .status,
    404,
  );
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/credentials",
      claims({ deployments: "active" }),
      VERIFIED,
    ).ok,
    true,
  );
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/unknown",
      claims(ALL_PRODUCTS_ACTIVE),
      VERIFIED,
    ).error,
    "route_not_allowed",
  );
  const paused = claims({ console: "paused" });
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/conversations/c-1",
      paused,
      VERIFIED,
    ).ok,
    true,
  );
  assert.deepEqual(
    decideProduct(TABLE, "POST", "/workspace/conversations", paused, VERIFIED),
    {
      status: 503,
      error: "product_paused",
      reason: "console",
      retryAfterSeconds: 30,
    },
  );
  assert.equal(
    decideProduct(
      TABLE,
      "POST",
      "/workspace/conversations/c-1/cancel",
      paused,
      VERIFIED,
    ).ok,
    true,
  );
  assert.equal(
    decideProduct(TABLE, "POST", "/workspace/approvals/a-1", paused, VERIFIED)
      .ok,
    true,
  );
  // A route two products serve stays open for new work while either is active.
  assert.equal(
    decideProduct(
      TABLE,
      "GET",
      "/workspace/credentials",
      claims({ console: "paused", deployments: "active" }),
      VERIFIED,
    ).ok,
    true,
  );
});

async function gateway(t, { exchange, jwks = JWKS } = {}) {
  const calls = { exchange: 0, upstream: [] };
  // Cloud mode requires the JWKS (and a shared limit store, injected here as memory); a gateway
  // without a JWKS can only run in local mode, on loopback.
  const config = loadConfig(
    jwks
      ? {
          OMEGA_REMOTE_BASE_URL: "https://omega.example.test",
          OMEGA_SESSION_EXCHANGE_URL: EXCHANGE,
          OMEGA_ASSERTION_JWKS_FILE: jwks,
          OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379",
        }
      : {
          OMEGA_GATEWAY_MODE: "local",
          OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:9",
          OMEGA_SESSION_EXCHANGE_URL: LOCAL_EXCHANGE,
        },
  );
  const server = createGatewayServer({
    config,
    rateLimiter: createRateLimiter(),
    log: () => {},
    fetchImpl: async (url, init) => {
      if (url === config.exchangeUrl) {
        calls.exchange += 1;
        return exchange
          ? exchange(init)
          : json({ accessToken: mintAssertion() });
      }
      calls.upstream.push({ url, method: init.method });
      return json({ ok: true });
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, path, authorization, headers = {}) => {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { authorization, ...headers },
    });
    return {
      status: response.status,
      body: await response.json().catch(() => null),
      retryAfter: response.headers.get("retry-after"),
    };
  };
  return { call, calls };
}

test("RC-03 at the gateway: B's key, an edited claim, no JWKS, HEAD, case, encoding and X-Forwarded-For", async (t) => {
  const { call, calls } = await gateway(t);
  const keyB = mintAssertion({ products: {} });
  const keyA2 = mintAssertion({ products: { console: "active" } });
  for (const [key, path] of [
    [keyB, "/workspace/sandboxes"],
    [keyB, "/workspace/conversations"],
    [keyB, "/workspace/capabilities"],
    [keyA2, "/workspace/sandboxes"],
    [
      mintAssertion({ products: undefined, rollout_epoch: undefined }),
      "/workspace/sandboxes",
    ],
  ]) {
    const answer = await call("GET", path, `Bearer ${key}`, {
      "x-forwarded-for": "10.0.0.1",
    });
    assert.equal(answer.status, 404, path);
    assert.equal(answer.body.error, "product_not_admitted");
  }
  // B edits its own key to name every product: the signature no longer matches.
  const [prefixAndHeader, , signature] = keyB.split(".");
  const edited = `${prefixAndHeader}.${Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(keyB.split(".")[1], "base64url")), products: ALL_PRODUCTS_ACTIVE })).toString("base64url")}.${signature}`;
  const forged = await call("GET", "/workspace/sandboxes", `Bearer ${edited}`);
  assert.equal(forged.status, 401);
  assert.equal(forged.body.reason, "bad_signature");
  // A path that is not a route at all is refused before any credential is read, with the same answer
  // for every caller, so it reveals nothing about any product: 403 route_not_allowed. An encoded
  // spelling of a product family names no product (default deny).
  for (const [method, path, reason] of [
    ["GET", "/WORKSPACE/sandboxes", undefined],
    ["GET", "/workspace/Sandboxes", undefined],
    // The manifest's encoding rule (lane 1.3) refuses an encoded unreserved letter before this wall.
    ["HEAD", "/workspace/sandboxes", undefined],
    ["GET", "/workspace/%73andboxes", "path_refused"],
    ["GET", "/workspace/conversations/c-1%2F..%2F..%2Fsandboxes", undefined],
  ]) {
    const answer = await call(method, path, `Bearer ${keyB}`);
    assert.equal(answer.status, 403, `${method} ${path}`);
    if (method !== "HEAD") assert.equal(answer.body.error, "route_not_allowed");
    if (reason) assert.equal(answer.body.reason, reason);
  }
  assert.equal(calls.upstream.length, 0, "nothing reached EE");
});

test("a gateway with no JWKS trusts no claim: 503 verification_unavailable, nothing reaches EE", async (t) => {
  const { call, calls } = await gateway(t, { jwks: null });
  const answer = await call(
    "GET",
    "/workspace/sandboxes",
    `Bearer ${mintAssertion()}`,
  );
  assert.equal(answer.status, 503);
  assert.equal(answer.body.error, "verification_unavailable");
  assert.equal(calls.upstream.length, 0);
});

test("Django's rollout refusals keep their meaning through the browser-token path", async (t) => {
  for (const [status, code] of [
    [404, "product_not_admitted"],
    [503, "verification_unavailable"],
  ]) {
    const { call, calls } = await gateway(t, {
      exchange: () => json({ code }, status),
    });
    const answer = await call(
      "GET",
      "/workspace/sandboxes",
      "Bearer browser-session",
    );
    assert.equal(answer.status, status);
    assert.equal(answer.body.error, code);
    assert.equal(calls.exchange, 1);
    assert.equal(calls.upstream.length, 0);
  }
});

test("A is admitted; paused keeps reads and refuses new work with 503 and Retry-After", async (t) => {
  const { call, calls } = await gateway(t);
  assert.equal(
    (await call("GET", "/workspace/sandboxes", `Bearer ${mintAssertion()}`))
      .status,
    200,
  );
  const paused = mintAssertion({
    products: { ...ALL_PRODUCTS_ACTIVE, sandboxes: "paused" },
  });
  assert.equal(
    (await call("GET", "/workspace/sandboxes/sb-1", `Bearer ${paused}`)).status,
    200,
  );
  const refused = await call(
    "POST",
    "/workspace/sandboxes/sb-1/exec",
    `Bearer ${mintAssertion({ products: { sandboxes: "paused" } })}`,
  );
  assert.equal(refused.status, 503);
  assert.equal(refused.body.error, "product_paused");
  assert.equal(refused.retryAfter, "30");
  // Other products are unaffected by the sandbox pause.
  assert.equal(
    (await call("POST", "/workspace/conversations", `Bearer ${paused}`)).status,
    200,
  );
  assert.deepEqual(
    calls.upstream.map((c) => c.method),
    ["GET", "GET", "POST"],
  );
});

// HIDE-API at W2: a feature the shipped route manifest hides (the switch EE's
// embedded Console registry also reads) answers every listed method
// 404 `not_found` in EE's own envelope (the body EE gives an Integrations app that is not offered),
// for every role and for two tenants, after the credential and the product wall and before the role
// floor, and is never forwarded. Deleting the manifest entry brings the same routes back.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { createGatewayServer } from "../src/server.mjs";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import {
  compileConsoleRouteManifest,
  MANIFEST_PATH,
} from "../src/manifest.mjs";
import { decideIdentity, decideRoute, ROUTE_MANIFEST } from "../src/routes.mjs";
import { json, mintAssertion, suiteJwksFile } from "./helpers.mjs";
import { visibleFamilyManifest } from "./visible-families.mjs";

const manifestBytes = fs.readFileSync(MANIFEST_PATH, "utf8");
const raw = JSON.parse(manifestBytes);
// Keep the imported positive composition distinct from Phase A's shipped absence
// contract. Enabling these four in a fixture retains G04's three hidden families.
const POSITIVE_FAMILIES = [
  "skills",
  "schedules",
  "learning-lessons",
  "integrations",
];
const positive = visibleFamilyManifest(...POSITIVE_FAMILIES);
const JWKS = suiteJwksFile();
const SAMPLE = { id: "x-1", revision: "2", schedule: "sch-1" };
const concrete = (template) =>
  template.replace(/\{([a-zA-Z]+)\}/g, (_, name) => SAMPLE[name]);
/** The keys the round-3 verifier logged for built-in app connect's 404 from EE: the parity target. */
const NOT_OFFERED_KEYS = [
  "schemaVersion",
  "error",
  "code",
  "message",
  "request_id",
];
const HIDDEN = ROUTE_MANIFEST.routes.filter(
  (route) => route.hiddenBy !== undefined,
);
const TENANT_B = {
  sub: "44444444-4444-4444-8444-444444444444",
  org_id: "55555555-5555-4555-8555-555555555555",
  workspace_id: "66666666-6666-4666-8666-666666666666",
};

const roomy = (capacity) =>
  Object.fromEntries(
    ["read", "write", "model-start", "public-share"].map((name) => [
      name,
      [{ scope: "user", capacity, perSecond: 0.001 }],
    ]),
  );

async function gateway(t, manifest, { capacity = 10_000 } = {}) {
  const upstream = [];
  const refusals = [];
  const config = loadConfig({
    OMEGA_REMOTE_BASE_URL: "https://omega.example.test",
    OMEGA_SESSION_EXCHANGE_URL:
      "https://futureagi.example.test/accounts/omega/session-exchange/",
    OMEGA_ASSERTION_JWKS_FILE: JWKS,
    OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL: "redis://redis.example.test:6379",
  });
  const server = createGatewayServer({
    config,
    rateLimiter: createRateLimiter({ classes: roomy(capacity) }),
    log: () => {},
    refusalLog: (line) => refusals.push(line),
    ...(manifest ? { manifest } : {}),
    fetchImpl: async (url, init) => {
      upstream.push({ url, method: init.method });
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
  const call = async (method, path, claims = {}, { token } = {}) => {
    const body = method === "GET" || method === "DELETE" ? undefined : "{}";
    const response = await fetch(`${base}${path}`, {
      method,
      body,
      headers: {
        ...(token === null
          ? {}
          : { authorization: `Bearer ${token ?? mintAssertion(claims)}` }),
        ...(body === undefined
          ? {}
          : { "content-type": "application/json", "idempotency-key": "k-1" }),
      },
    });
    return {
      status: response.status,
      body: await response.json().catch(() => null),
      requestId: response.headers.get("x-request-id"),
    };
  };
  return { call, upstream, refusals };
}

test("the shipped Phase A manifest hides exactly 44 routes of 7 families", () => {
  const counts = {
    "agent-teams": 4,
    environments: 7,
    integrations: 10,
    "learning-lessons": 3,
    schedules: 6,
    "signed-triggers": 7,
    skills: 7,
  };
  assert.deepEqual(
    ROUTE_MANIFEST.hidden.map((feature) => feature.id).sort(),
    Object.keys(counts),
  );
  assert.equal(HIDDEN.length, 44);
  for (const [family, count] of Object.entries(counts)) {
    assert.equal(
      HIDDEN.filter((route) => route.hiddenBy === family).length,
      count,
      family,
    );
  }
  const prefixes = ROUTE_MANIFEST.hidden.flatMap((feature) => feature.paths);
  for (const route of ROUTE_MANIFEST.routes) {
    assert.equal(
      route.hiddenBy !== undefined,
      prefixes.some(
        (prefix) => route.path === prefix || route.path.startsWith(`${prefix}/`),
      ),
      `${route.method} ${route.path}`,
    );
  }
  // Building positive fixtures neither changes the bytes nor unhides any other
  // family. A misspelled opt-in must fail, not silently test the wrong state.
  assert.equal(fs.readFileSync(MANIFEST_PATH, "utf8"), manifestBytes);
  assert.throws(() => visibleFamilyManifest("skils"), /Unknown hidden family/);
});

test("the manifest hides 18 routes of 3 features, every method the manifest lists on their paths", () => {
  // Historical positive composition: skills and schedules serve; agent teams,
  // environments and signed triggers stay hidden. Shipped absence is tested above.
  assert.deepEqual(positive.hidden.map((feature) => feature.id).sort(), [
    "agent-teams",
    "environments",
    "signed-triggers",
  ]);
  const hidden = positive.routes.filter((route) => route.hiddenBy !== undefined);
  assert.equal(hidden.length, 18);
  assert.equal(
    hidden.filter((route) => route.hiddenBy === "signed-triggers").length,
    7,
  );
  const prefixes = positive.hidden.flatMap((feature) => feature.paths);
  for (const route of positive.routes) {
    const under = prefixes.some(
      (prefix) => route.path === prefix || route.path.startsWith(`${prefix}/`),
    );
    assert.equal(
      route.hiddenBy !== undefined,
      under,
      `${route.method} ${route.path}`,
    );
  }
  // The role floor is skipped for a hidden route (it answers 404 for every role), kept for the rest: a shown
  // feature's admin route (POST /workspace/skills) refuses a member again.
  assert.deepEqual(
    decideIdentity(decideRoute("POST", "/workspace/environments", positive), {
      role: "member",
      org_id: "o",
      workspace_id: "w",
    }),
    { allowed: true },
  );
  assert.deepEqual(
    decideIdentity(decideRoute("POST", "/workspace/skills", positive), {
      role: "member",
      org_id: "o",
      workspace_id: "w",
    }),
    { allowed: false, reason: "role_below_floor" },
  );
  assert.deepEqual(
    decideIdentity(decideRoute("POST", "/workspace/workforce/agent-drafts", positive), {
      role: "member",
      org_id: "o",
      workspace_id: "w",
    }),
    { allowed: false, reason: "role_below_floor" },
  );
});

test("every hidden route is 404 not_found in EE's envelope for an owner, a member and tenant B, and nothing reaches EE", async (t) => {
  const { call, upstream, refusals } = await gateway(t);
  for (const route of HIDDEN) {
    for (const [who, claims] of [
      ["owner A", { role: "owner" }],
      ["member A", { role: "member" }],
      ["owner B", { ...TENANT_B, role: "owner" }],
    ]) {
      const answer = await call(route.method, concrete(route.path), claims);
      const where = `${who} ${route.method} ${route.path}`;
      assert.equal(answer.status, 404, where);
      assert.deepEqual(Object.keys(answer.body), NOT_OFFERED_KEYS, where);
      assert.deepEqual(
        { ...answer.body, request_id: undefined },
        {
          schemaVersion: "omega-workspace/v1",
          error: { code: "not_found", message: "Not found." },
          code: "not_found",
          message: "Not found.",
          request_id: undefined,
        },
        where,
      );
      assert.equal(answer.body.request_id, answer.requestId, where);
    }
  }
  assert.equal(upstream.length, 0, "no hidden request was forwarded");
  assert.equal(refusals.length, HIDDEN.length * 3);
  assert.ok(
    refusals.every(
      (line) =>
        line.status === 404 &&
        line.error === "not_found" &&
        line.reason === "feature_hidden",
    ),
  );
});

test("a hidden route is rate limited like any route of its class (a 404 still spends the caller's token)", async (t) => {
  const { call, upstream } = await gateway(t, undefined, { capacity: 1 });
  assert.equal((await call("POST", "/workspace/environments")).status, 404);
  const over = await call("POST", "/workspace/environments");
  assert.equal(over.status, 429);
  assert.equal(over.body.error, "omega_rate_limited");
  assert.equal(upstream.length, 0);
});

test("the refusals before it are unchanged: no key is 401, a dark product is 404 product_not_admitted, an unlisted method is 403", async (t) => {
  const { call, upstream } = await gateway(t);
  assert.equal(
    (await call("GET", "/workspace/skills", {}, { token: null })).status,
    401,
  );
  const dark = await call("GET", "/workspace/schedules", {
    products: { sandboxes: "active" },
  });
  assert.equal(dark.status, 404);
  assert.equal(dark.body.error, "product_not_admitted");
  for (const [method, path] of [
    ["PUT", "/workspace/skills"],
    ["GET", "/workspace/schedules/events"],
    ["DELETE", "/workspace/environments/env-1"],
  ]) {
    const answer = await call(method, path);
    assert.equal(answer.status, 403, `${method} ${path}`);
    assert.equal(answer.body.error, "route_not_allowed");
  }
  // Shipped visible routes still reach EE.
  assert.equal((await call("GET", "/workspace/usage")).status, 200);
  assert.equal(
    (await call("GET", "/workspace/workforce", { ...TENANT_B, role: "member" }))
      .status,
    200,
  );
  // Preserve the skills/schedules positive forwarding checks in their explicit
  // ON fixture, without changing any of the shipped-state refusals above.
  const shown = await gateway(t, visibleFamilyManifest("skills", "schedules"));
  assert.equal(
    (await shown.call("GET", "/workspace/skills", { role: "member" })).status,
    200,
  );
  assert.equal(
    (await shown.call("GET", "/workspace/schedules", { ...TENANT_B, role: "member" }))
      .status,
    200,
  );
  assert.deepEqual(
    [...upstream, ...shown.upstream].map((entry) => `${entry.method} ${new URL(entry.url).pathname}`),
    [
      "GET /workspace/usage",
      "GET /workspace/workforce",
      "GET /workspace/skills",
      "GET /workspace/schedules",
    ],
  );
});

test("deleting a feature's hidden entry brings its routes back at their role floor (the other state of the one switch)", async (t) => {
  // Only this positive fixture opens withdrawn families; the unmodified manifest
  // is still exercised for every hidden route, role and tenant above.
  const reopened = visibleFamilyManifest(...POSITIVE_FAMILIES, "environments");
  assert.equal(
    reopened.routes.filter((route) => route.hiddenBy !== undefined).length,
    11,
  );
  assert.equal(
    reopened.routes.filter((route) => route.hiddenBy === "signed-triggers")
      .length,
    7,
  );
  const { call, upstream } = await gateway(t, reopened);
  assert.equal((await call("GET", "/workspace/environments")).status, 200);
  assert.equal(
    (
      await call("POST", "/workspace/environments/env-1/control", {
        role: "member",
      })
    ).status,
    200,
  );
  assert.equal(
    (await call("GET", "/workspace/agent-teams")).status,
    404,
    "agent teams stay hidden",
  );
  // Both states of skills' switch: listed, all 7 routes are hidden (as shipped);
  // in the positive fixture PATCH passes at its admin floor and a member's POST is refused.
  const listed = compileConsoleRouteManifest({
    ...raw,
    hidden: {
      ...raw.hidden,
      skills: { until: "a test reason", paths: ["/workspace/skills"] },
    },
  });
  assert.equal(
    listed.routes.filter((route) => route.hiddenBy === "skills").length,
    7,
  );
  assert.equal(
    (await call("PATCH", "/workspace/skills/s-1", { role: "admin" })).status,
    200,
  );
  const member = await call("POST", "/workspace/skills", { role: "member" });
  assert.equal(member.status, 403);
  assert.equal(member.body.error, "omega_role_not_permitted");
  assert.deepEqual(
    upstream.map((entry) => entry.method),
    ["GET", "POST", "PATCH"],
  );
  // With no hidden entry at all, nothing is hidden.
  assert.equal(
    compileConsoleRouteManifest({ ...raw, hidden: {} }).routes.some(
      (route) => route.hiddenBy !== undefined,
    ),
    false,
  );
});

test("opening only environments leaves every withdrawn Phase A family hidden", async (t) => {
  const reopened = visibleFamilyManifest("environments");
  assert.equal(reopened.routes.filter((route) => route.hiddenBy !== undefined).length, 37);
  assert.deepEqual(reopened.hidden.map((feature) => feature.id).sort(), [
    "agent-teams", "integrations", "learning-lessons", "schedules", "signed-triggers", "skills",
  ]);
  const { call, upstream } = await gateway(t, reopened);
  assert.equal((await call("GET", "/workspace/environments")).status, 200);
  const forwarded = [...upstream];
  for (const route of reopened.routes.filter((entry) => entry.hiddenBy !== undefined)) {
    const answer = await call(route.method, concrete(route.path), { role: "owner" });
    assert.equal(answer.status, 404, `${route.method} ${route.path}`);
    assert.equal(answer.body.code, "not_found");
    assert.deepEqual(Object.keys(answer.body), NOT_OFFERED_KEYS);
  }
  assert.deepEqual(upstream, forwarded, "opening environments forwards no other hidden family");
});

test("a typo in hidden fails the compile instead of hiding nothing or too much", () => {
  const withHidden = (hidden) => () =>
    compileConsoleRouteManifest({ ...raw, hidden });
  const { hidden: _drop, ...noHidden } = raw;
  assert.throws(
    () => compileConsoleRouteManifest(noHidden),
    /missing or unknown fields/,
  );
  assert.throws(
    withHidden({
      skills: { until: "x", paths: ["/workspace/skills"], extra: 1 },
    }),
    /unknown fields/,
  );
  assert.throws(
    withHidden({ skills: { until: " ", paths: ["/workspace/skills"] } }),
    /needs an until/,
  );
  assert.throws(
    withHidden({ skills: { until: "x", paths: [] } }),
    /needs paths/,
  );
  assert.throws(
    withHidden({ skills: { until: "x", paths: ["/workspace/skils"] } }),
    /covers no browser route/,
  );
  assert.throws(
    withHidden({ skills: { until: "x", paths: ["/workspace/{id}"] } }),
    /invalid path/,
  );
  assert.throws(
    withHidden({ skills: { until: "x", paths: ["/auth/orgs"] } }),
    /invalid path/,
  );
  assert.throws(
    withHidden({
      a: { until: "x", paths: ["/workspace/skills"] },
      b: { until: "x", paths: ["/workspace/skills"] },
    }),
    /listed twice/,
  );
  assert.throws(
    withHidden({
      outer: { until: "x", paths: ["/workspace/workforce"] },
      inner: { until: "x", paths: ["/workspace/workforce/agents"] },
    }),
    /hidden by two features/,
  );
  assert.throws(
    withHidden({ Bad: { until: "x", paths: ["/workspace/usage"] } }),
    /Invalid hidden feature/,
  );
});

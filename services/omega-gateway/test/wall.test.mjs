// W2 contract test (CP P3, P4, SEC2; READINESS L04): the gateway admits exactly the manifest's
// browser routes and refuses everything else, and its copy of the manifest is the one EE has.
import test from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileConsoleRouteManifest, MANIFEST_PATH } from "../src/manifest.mjs";
import { decideIdentity, decideRoute, ROUTE_MANIFEST_SHA256 } from "../src/routes.mjs";
import { visibleFamilyManifest } from "./visible-families.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const raw = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
const METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"];
const SAMPLE = {
  id: "id-1", revision: "3", deployment: "dep-0123456789abcdef", sandbox: "sbx-1", schedule: "sch-1",
  filePath: "src/my%20file.txt", org: "22222222-2222-4222-8222-222222222222", connector: "github", rest: "conn-1/connect",
  // An agent computer's id, as the manifest's computerId pattern admits it (SB-AGENT-COMPUTER-CORE, integration round 4).
  computerId: "cmp_0123456789abcdef01234567", terminal: "0123abcd-0123-4abc-8def-0123456789ab", artifact: `sha256-${"a".repeat(64)}`,
};
const concrete = (template) => template.replace(/\{([a-zA-Z]+)\}/g, (_, name) => SAMPLE[name]);
const listed = new Set(raw.routes.map((route) => `${route.method} ${route.path}`));

test("every manifest browser route is admitted for its method, with its classes", () => {
  assert.ok(raw.routes.length > 100);
  for (const route of raw.routes) {
    const decision = decideRoute(route.method, concrete(route.path));
    assert.equal(decision.allowed, true, `${route.method} ${route.path}`);
    assert.equal(decision.route.path, route.path, `${route.method} ${concrete(route.path)} matched another template`);
    for (const key of ["role", "body", "timeout", "rate"]) assert.equal(decision.route[key], route[key]);
    assert.equal(decision.route.credential, route.credential ?? "key");
  }
});

test("no other method reaches a manifest path (default deny per method)", () => {
  for (const route of raw.routes) {
    const sample = concrete(route.path);
    for (const method of METHODS) {
      const decision = decideRoute(method, sample);
      if (!decision.allowed) continue;
      assert.ok(listed.has(`${method} ${decision.route.path}`), `${method} ${sample} admitted without a manifest entry`);
    }
  }
});

test("no eeOnly route is reachable through the gateway, by any method", () => {
  assert.ok(raw.eeOnly.length > 50);
  for (const route of raw.eeOnly) {
    for (const method of METHODS) assert.equal(decideRoute(method, concrete(route.path)).allowed, false, `${method} ${route.path}`);
  }
});

test("decided closures: P4 writes, D20 verbs closed; retry, agent DELETE and conversation DELETE open (D20, D21, B9)", () => {
  for (const [method, sample] of [
    ["PATCH", "/auth/orgs/org-1/members/acct-2"], ["DELETE", "/auth/orgs/org-1/members/acct-2"],
    ["POST", "/auth/orgs/org-1/invites"], ["DELETE", "/auth/orgs/org-1/invites/inv-1"],
    ["POST", "/billing/accounts/acct-1/checkout"], ["POST", "/billing/accounts/acct-1/subscription/cancel"],
    ["POST", "/billing/accounts/acct-1/seats"], ["POST", "/billing/accounts/acct-1/credits"],
    ...["pause", "resume", "stop", "kill"].map((verb) => ["POST", `/workspace/conversations/c-1/${verb}`]),
  ]) {
    assert.equal(decideRoute(method, sample).allowed, false, `${method} ${sample}`);
  }
  assert.equal(decideRoute("POST", "/workspace/conversations/c-1/retry").allowed, true);
  assert.equal(decideRoute("DELETE", "/workspace/conversations/c-1").route.role, "member");
  assert.equal(decideRoute("DELETE", "/workspace/workforce/agents/a-1").route.role, "admin");
});

test("default deny: a route that is not in the manifest is refused, whatever it looks like", () => {
  for (const [method, sample] of [
    ["GET", "/workspace/new-family"], ["GET", "/workspace/conversations/"], ["GET", "/workspace"], ["GET", "/"],
    ["POST", "/workspace/capabilities"], ["GET", "/workspace/conversations/c-1/secret"], ["GET", "/WORKSPACE/usage"],
  ]) {
    const decision = decideRoute(method, sample);
    assert.equal(decision.allowed, false, `${method} ${sample}`);
    assert.equal(decision.reason, "route_not_in_manifest");
  }
  // Removing one entry closes it: admission comes from the list and nothing else.
  const trimmed = { ...raw, routes: raw.routes.filter((route) => !(route.method === "GET" && route.path === "/workspace/usage")) };
  assert.equal(decideRoute("GET", "/workspace/usage", compileConsoleRouteManifest(trimmed)).allowed, false);
  assert.equal(decideRoute("GET", "/workspace/usage").allowed, true);
});

test("the % rule: encoded names open; encoded separators, dots, percent, controls, unreserved characters and # never do", () => {
  assert.equal(decideRoute("GET", "/workspace/sandboxes/sbx-1/files/my%20file.txt").allowed, true);
  assert.equal(decideRoute("GET", "/workspace/sandboxes/sbx%201").allowed, true);
  // What encodeURIComponent produces for a file name still opens (it never encodes A-Z a-z 0-9 - . _ ~).
  const name = "naïve plan (v2)!.md";
  assert.equal(decideRoute("GET", `/workspace/sandboxes/sbx-1/files/${encodeURIComponent(name)}`).allowed, true);
  // Spelled plainly, `settings` is the literal: PATCH is not admitted on it, so its encoded
  // spelling (below) must not reach the `{sandbox}` route either.
  assert.deepEqual(decideRoute("PATCH", "/workspace/sandboxes/settings"), { allowed: false, reason: "route_not_in_manifest" });
  assert.equal(decideRoute("GET", "/workspace/sandboxes/settings").route.path, "/workspace/sandboxes/settings");
  for (const sample of [
    "/workspace/sandboxes/sbx-1/files/a%2Fb", "/workspace/sandboxes/sbx-1/files/a%2fb", "/workspace/sandboxes/sbx-1%5Cx",
    "/workspace/sandboxes/%2E%2E/x", "/workspace/sandboxes/sbx-1/files/%2e", "/workspace/sandboxes/sbx-1/files/a%00",
    "/workspace/sandboxes/sbx-1/files/a%252F", "/workspace/sandboxes/sbx-1/files/a%zz", "/workspace/sandboxes/sbx-1/files/a%",
    "/workspace//sandboxes", "/workspace/./sandboxes", "/workspace/../os/agents", "/workspace\\sandboxes", "workspace/sandboxes",
    "/workspace/sandboxes/sbx-1/files/a\u0001",
    // A fragment would be cut by fetch on the way to EE (verifier finding, 2026-10-17).
    "/workspace/conversations/c1#/messages", "/workspace/approvals/#x", "/workspace/conversations/c1#",
    "/workspace/sandboxes/sbx-1/files/a#b",
    // An encoded unreserved character can only disguise a literal: %73ettings is `settings`.
    "/workspace/sandboxes/%73ettings", "/workspace/sandboxes/%53ettings", "/workspace/sandboxes/sbx%2D1",
    "/workspace/sandboxes/sbx-1/files/%61.txt", "/workspace/sandboxes/sbx-1/files/a%5F", "/workspace/sandboxes/sbx-1/files/a%7e",
  ]) {
    const decision = decideRoute("GET", sample);
    assert.equal(decision.allowed, false, sample);
    assert.equal(decision.reason, "path_refused", sample);
  }
});

test("role floors and scope parameters are checked against the caller's claims", () => {
  const draft = decideRoute("POST", "/workspace/workforce/agent-drafts");
  assert.equal(draft.route.role, "admin");
  assert.deepEqual(decideIdentity(draft, { role: "member", org_id: "o", workspace_id: "w" }), { allowed: false, reason: "role_below_floor" });
  assert.deepEqual(decideIdentity(draft, { role: "admin", org_id: "o", workspace_id: "w" }), { allowed: true });
  assert.deepEqual(decideIdentity(draft, { role: "owner", org_id: "o", workspace_id: "w" }), { allowed: true });
  // OM8: when Integrations is enabled, access control is the owners' alone, so an
  // admin cannot lift a restriction an owner set. hidden.test.mjs separately pins
  // the shipped family's 404 absence; do not replace these positive role checks.
  const access = decideRoute("PUT", "/workspace/integrations/access", visibleFamilyManifest("integrations"));
  assert.equal(access.route.role, "owner");
  assert.deepEqual(decideIdentity(access, { role: "admin", org_id: "o", workspace_id: "w" }), { allowed: false, reason: "role_below_floor" });
  assert.deepEqual(decideIdentity(access, { role: "member", org_id: "o", workspace_id: "w" }), { allowed: false, reason: "role_below_floor" });
  assert.deepEqual(decideIdentity(access, { role: "owner", org_id: "o", workspace_id: "w" }), { allowed: true });
  // A browser route with a scope parameter must name the caller's own org.
  const scoped = compileConsoleRouteManifest({ ...raw, routes: [...raw.routes, { method: "GET", path: "/workspace/orgs/{org}/summary", role: "member", body: "none", timeout: "read", rate: "read" }] });
  const decision = decideRoute("GET", "/workspace/orgs/org-a/summary", scoped);
  assert.deepEqual(decideIdentity(decision, { role: "member", org_id: "org-a", workspace_id: "w" }), { allowed: true });
  assert.deepEqual(decideIdentity(decision, { role: "member", org_id: "org-b", workspace_id: "w" }), { allowed: false, reason: "scope_pivot" });
});

test("the compiler is strict: a typo can never widen the wall", () => {
  const bad = [
    { ...raw, extra: true },
    { ...raw, routes: [...raw.routes, { ...raw.routes[0] }] },
    { ...raw, routes: [{ ...raw.routes[0], rate: "unlimited" }] },
    { ...raw, routes: [{ ...raw.routes[0], role: "viewer" }] },
    { ...raw, routes: [{ ...raw.routes[0], path: "/os/agents" }] },
    { ...raw, routes: [{ ...raw.routes[0], path: "/workspace/{nope}" }] },
    { ...raw, routes: [{ ...raw.routes[0], path: "/workspace/Caps" }] },
    { ...raw, routes: [{ ...raw.routes[0], method: "HEAD" }] },
    { ...raw, routes: [{ ...raw.routes[0], note: "x" }] },
    { ...raw, schemaVersion: "console-route-manifest/v2" },
    { ...raw, eeOnly: [{ ...raw.eeOnly[0], path: "/workspace/usage" }] },
  ];
  for (const manifest of bad) assert.throws(() => compileConsoleRouteManifest(manifest));
});

test("the manifest bytes match the pinned sha256, and EE's copy when it is available (SEC2)", () => {
  const pinned = fs.readFileSync(path.join(here, "..", "manifest", "console-route-manifest.v1.sha256"), "utf8").trim().split(/\s+/)[0];
  assert.equal(ROUTE_MANIFEST_SHA256, pinned);
  assert.equal(crypto.createHash("sha256").update(fs.readFileSync(MANIFEST_PATH)).digest("hex"), pinned);
  const eeCopy = process.env.OMEGA_EE_ROUTE_MANIFEST;
  if (eeCopy) {
    assert.equal(crypto.createHash("sha256").update(fs.readFileSync(eeCopy)).digest("hex"), pinned, `EE copy ${eeCopy} differs`);
  }
});

test("no hand-written route rule exists outside the manifest compiler", () => {
  const src = path.join(here, "..", "src");
  // Every module, the channel-token ones (CX-WALL) included.
  for (const file of fs.readdirSync(src, { recursive: true })) {
    if (file === "manifest.mjs" || !file.endsWith(".mjs")) continue;
    const text = fs.readFileSync(path.join(src, file), "utf8");
    for (const family of ["workspace", "auth", "billing", "files", "os"]) {
      assert.doesNotMatch(text, new RegExp(String.raw`\\\/${family}\b`), `${file} has a route regex for /${family}`);
      assert.doesNotMatch(text, new RegExp(String.raw`["'\`]\/${family}\/`), `${file} has a route literal for /${family}`);
    }
  }
});

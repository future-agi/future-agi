import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import { test as playwrightTest } from "@playwright/test";
import ts from "typescript";

// The real provisioning.ts and ApiClient; only the HTTP context is faked.
// lib modules import each other without an extension, and ApiClient uses
// parameter properties, so resolve to .ts and compile with E2E's TypeScript.
const lib = new URL("../lib/", import.meta.url).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.startsWith(lib) && /^\.\.?\/[^.]+$/.test(specifier))
      return nextResolve(`${specifier}.ts`, context);
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (!url.startsWith(lib) || !url.endsWith(".ts")) return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.ESNext,
        },
      }).outputText,
    };
  },
});
const { provisionActor } = await import("../lib/provisioning.ts");

const SIGNUP = "/accounts/e2e/clean-signup/";
const response = (status, body) => ({
  status: () => status,
  text: async () => JSON.stringify(body),
});
const reset = () =>
  new Error(
    "apiRequestContext.post: socket hang up\nCall log:\n  - → POST http://backend/accounts/e2e/clean-signup/",
  );

// A backend that answers the provisioning calls. `signups` scripts the outcome
// of each signup POST, in order: an Error is thrown, anything else is returned.
function backend(signups) {
  const posts = [];
  const context = {
    post: async (url, options) => {
      const path = new URL(url).pathname;
      posts.push({ path, data: options.data });
      if (path === SIGNUP) {
        const outcome = signups.shift();
        assert.ok(outcome, "unexpected extra signup");
        if (outcome instanceof Error) throw outcome;
        return outcome;
      }
      return response(200, { access: "access", refresh: "refresh" });
    },
    get: async (url) =>
      new URL(url).pathname === "/accounts/keys/"
        ? response(200, {
            status: "ok",
            data: { api_key: "key", secret_key: "secret" },
          })
        : response(200, {
            organization: { id: "org" },
            default_workspace_id: "workspace",
          }),
  };
  return {
    context,
    signupsSent: () => posts.filter((post) => post.path === SIGNUP),
    tokenRequests: () =>
      posts.filter((post) => post.path === "/accounts/token/"),
  };
}

function annotations(t) {
  const entries = [];
  t.mock.method(playwrightTest, "info", () => ({ annotations: entries }));
  return entries;
}

test("a signup that answers is sent once", async (t) => {
  const entries = annotations(t);
  const stack = backend([response(200, { status: "success" })]);
  const actor = await provisionActor(stack.context, "unit");
  assert.equal(stack.signupsSent().length, 1);
  assert.equal(actor.email, stack.signupsSent()[0].data.email);
  assert.deepEqual(entries, []);
});

test("a reset without a response retries once, as a new identity", async (t) => {
  const entries = annotations(t);
  const stack = backend([reset(), response(200, { status: "success" })]);
  const actor = await provisionActor(stack.context, "unit", {
    keyAdministrator: true,
  });
  const [first, second] = stack.signupsSent();
  assert.equal(stack.signupsSent().length, 2);
  // The first account may or may not exist; the retry must not depend on it.
  assert.notEqual(first.data.email, second.data.email);
  assert.notEqual(first.data.password, second.data.password);
  assert.equal(second.data.key_administrator, true);
  // Everything after the signup uses the identity that was confirmed.
  assert.equal(actor.email, second.data.email);
  assert.equal(actor.password, second.data.password);
  assert.deepEqual(stack.tokenRequests()[0].data, {
    email: second.data.email,
    password: second.data.password,
    remember_me: true,
  });
  assert.deepEqual(entries, [
    {
      type: "api-transport-retry",
      description: `POST ${SIGNUP}: apiRequestContext.post: socket hang up`,
    },
  ]);
});

test("a second reset fails the actor", async (t) => {
  annotations(t);
  const stack = backend([reset(), reset()]);
  await assert.rejects(provisionActor(stack.context, "unit"), /socket hang up/);
  assert.equal(stack.signupsSent().length, 2);
  assert.equal(stack.tokenRequests().length, 0);
});

test("an HTTP error, a timeout or a reset after a response is never retried", async (t) => {
  const entries = annotations(t);
  const afterResponse = new Error(
    "apiRequestContext.post: socket hang up\nCall log:\n  - ← 502 Bad Gateway",
  );
  for (const [outcome, expected] of [
    [response(500, { error: "boom" }), /API 500/],
    [response(400, { error: "A password is required" }), /API 400/],
    [new Error("apiRequestContext.post: Timeout 30000ms exceeded"), /Timeout/],
    [afterResponse, /socket hang up/],
    [new Error("connect ECONNREFUSED 127.0.0.1:8100"), /ECONNREFUSED/],
  ]) {
    const stack = backend([outcome]);
    await assert.rejects(provisionActor(stack.context, "unit"), expected);
    assert.equal(stack.signupsSent().length, 1);
  }
  assert.deepEqual(entries, []);
});

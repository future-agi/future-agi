import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { cloudEnv, json, mintAssertion, startGateway } from "./helpers.mjs";
import { loadConfig } from "../src/policy.mjs";
import {
  compileConsoleRouteManifest,
  MANIFEST_PATH,
} from "../src/manifest.mjs";

function released() {
  const source = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  delete source.hidden["signed-triggers"];
  return compileConsoleRouteManifest(source);
}

test("signed event bytes reach upstream without a bearer or session exchange", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    manifest: released(),
    handler: {
      upstream: (_url, init) => json({ received: init.body.toString() }),
    },
  });
  const raw = '{ "input" : "synthetic event" }';
  for (const authorization of [
    undefined,
    "Bearer not-a-valid-session",
    `Bearer ${mintAssertion({ role: "admin" })}`,
  ]) {
    const result = await fetch(`${base}/workspace/triggers/trigger-a/events`, {
      method: "POST",
      body: raw,
      headers: {
        ...(authorization ? { authorization } : {}),
        "content-type": "application/json",
        "x-omega-event-id": "event-a",
        "x-omega-timestamp": "2000000000",
        "x-omega-trigger-signature": "synthetic-signature",
        "x-omega-org": "ignored-organization",
      },
    });
    assert.equal(result.status, 200);
    assert.deepEqual(await result.json(), { received: raw });
    const sent = calls.upstream.at(-1).init;
    assert.equal(sent.headers["x-omega-event-id"], "event-a");
    assert.equal(sent.headers["x-omega-timestamp"], "2000000000");
    assert.equal(
      sent.headers["x-omega-trigger-signature"],
      "synthetic-signature",
    );
    assert.equal(sent.headers["x-omega-org"], undefined);
    assert.equal(sent.headers.authorization, undefined);
    assert.equal(result.headers.get("cache-control"), "no-store");
  }
  assert.equal(calls.exchange.length, 0);
});

test("member trigger management is refused before forwarding", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    manifest: released(),
  });
  for (const [method, route] of [
    ["GET", "/workspace/triggers"],
    ["POST", "/workspace/triggers"],
    ["POST", "/workspace/triggers/trigger-a/rotate"],
    ["DELETE", "/workspace/triggers/trigger-a"],
    ["GET", "/workspace/triggers/trigger-a/receipts"],
  ]) {
    const response = await fetch(`${base}${route}`, {
      method,
      headers: { authorization: `Bearer ${mintAssertion()}` },
    });
    assert.equal(response.status, 403, `${method} ${route}`);
    assert.equal((await response.json()).reason, "role_below_floor");
  }
  assert.equal(calls.upstream.length, 0);
});

test("event ingress is hidden by default without needing a bearer", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
  });
  const response = await fetch(`${base}/workspace/triggers/trigger-a/events`, {
    method: "POST",
    body: "{}",
  });
  assert.equal(response.status, 404);
  assert.equal(calls.upstream.length, 0);
  assert.equal(calls.exchange.length, 0);
});

test("event ingress uses a source-IP verification budget before forwarding", async (t) => {
  const seen = [];
  const rateLimiter = {
    close() {},
    storeKind: "test",
    async take(kind, identity) {
      seen.push({ kind, identity });
      return { allowed: false, retryAfterSeconds: 3, store: "test" };
    },
  };
  const logs = [];
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
    manifest: released(),
    rateLimiter,
    log: (row) => logs.push(row),
  });
  const response = await fetch(`${base}/workspace/triggers/trigger-a/events`, {
    method: "POST",
    body: "{}",
  });
  assert.equal(response.status, 429);
  const refusal = await response.json();
  assert.equal(refusal.rateClass, "trigger-ingress");
  assert.deepEqual(refusal.budgets, {
    sourceIpPerMinute: 60,
    triggerPerMinute: 20,
    workspacePerMinute: 120,
    verifiedConcurrencyPerTrigger: 4,
    verificationConcurrencyPerProcess: 32,
    runBudgetScope: "process",
  });
  assert.equal(response.headers.get("retry-after"), "3");
  assert.equal(logs.at(-1).rateClass, "trigger-ingress");
  assert.equal(seen.length, 1);
  assert.equal(seen[0].kind, "trigger-ingress");
  assert.equal(seen[0].identity.ip, "127.0.0.1");
  assert.equal(seen[0].identity.sub, undefined);
  assert.equal(seen[0].identity.workspaceId, undefined);
  assert.equal(calls.upstream.length, 0);
});

test("one source cannot spend another source's pre-verification budget", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: { ...loadConfig(cloudEnv(t)), trustedProxyHops: 1 },
    manifest: released(),
  });
  for (let i = 0; i < 61; i++) {
    const response = await fetch(
      `${base}/workspace/triggers/trigger-a/events`,
      {
        method: "POST",
        body: "{}",
        headers: { "x-forwarded-for": "192.0.2.1" },
      },
    );
    assert.equal(response.status, i < 60 ? 200 : 429);
    await response.arrayBuffer();
  }
  assert.equal(calls.upstream.length, 60);
  const legitimate = await fetch(
    `${base}/workspace/triggers/trigger-a/events`,
    { method: "POST", body: "{}", headers: { "x-forwarded-for": "192.0.2.2" } },
  );
  assert.equal(legitimate.status, 200);
  await legitimate.arrayBuffer();
  assert.equal(calls.upstream.length, 61);
});

test("only the event POST may declare signature ingress", () => {
  const source = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  assert.equal(
    source.routes.filter((r) => r.credential === "trigger-signature").length,
    1,
  );
  assert.equal(
    source.routes.find((r) => r.credential === "trigger-signature").rate,
    "trigger-ingress",
  );
  source.routes.find(
    (r) => r.path === "/workspace/triggers" && r.method === "GET",
  ).credential = "trigger-signature";
  assert.throws(
    () => compileConsoleRouteManifest(source),
    /cannot use trigger signatures/,
  );
});

test("event metadata is not forwarded to other routes", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)),
  });
  const result = await fetch(`${base}/workspace/usage`, {
    headers: {
      authorization: `Bearer ${mintAssertion()}`,
      "x-omega-event-id": "event-a",
      "x-omega-trigger-signature": "synthetic-signature",
    },
  });
  assert.equal(result.status, 200);
  assert.equal(calls.upstream[0].init.headers["x-omega-event-id"], undefined);
  assert.equal(
    calls.upstream[0].init.headers["x-omega-trigger-signature"],
    undefined,
  );
});

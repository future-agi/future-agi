// Human-session-only verbs (sandbox-better PLAN L3 and W2-ACCESS, critique C2-3 "API key can
// self-approve"; SB W2-KEYS fix round 2): an assertion that names `api_key` gets 403 on deciding
// an approval and on writing sandbox settings, before the limiter and the body, and nothing
// reaches EE; a browser session is forwarded as before, and a key still reads both.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createRateLimiter } from "../src/ratelimit.mjs";
import { ROUTE_MANIFEST } from "../src/routes.mjs";
import {
  checkSessionOnlyRoutes,
  decideSessionOnly,
  SESSION_ONLY_ROUTES,
} from "../src/session-only.mjs";
import {
  claimsOf,
  cloudEnv,
  json,
  mintAssertion,
  mintKeyAssertion,
  startGateway,
} from "./helpers.mjs";

const configFor = (t, extra) => loadConfig(cloudEnv(t, extra));
const KEY = { "x-api-key": "pub-123", "x-secret-key": "secret-123" };
const APPROVAL = "/workspace/approvals/appr-1";
const SETTINGS = "/workspace/sandboxes/settings";
const SETTINGS_DOC = JSON.stringify({ settings: { defaultNetwork: "none" } });
const OBSERVE_SETTINGS = "/workspace/observe/settings";
const OBSERVE_DOC = JSON.stringify({ contentCapture: "on" });
const REFUSED = {
  error: "omega_human_session_required",
  reason: "session_only_verb",
};

// Non-GET routes whose path names an approval decision, sharing, a share link, settings, an
// agent's learning or a workspace credential: the verbs W2-ACCESS reserves for a person.
// W2-ACCESS adds its sharing and share-link routes to the byte-identical manifest, and this test
// then fails until the gateway table lists them; so does any new write under an agent's learning
// or a workspace credential.
const LOOKS_SESSION_ONLY =
  /\/approvals\/\{|shar(?:e|ing)|settings|\/otlp(?:\/test)?$|^\/workspace\/learning\/|^\/workspace\/credentials(?:\/|$)/;
const lookalikes = (routes) =>
  routes
    .filter(
      (route) => route.method !== "GET" && LOOKS_SESSION_ONLY.test(route.path),
    )
    .map((route) => `${route.method} ${route.path}`);

// Minting the conversation computer's channel token is a person's act too.
const CHANNEL_MINT = "POST /workspace/conversations/{id}/computer/channel";
// Removing one of an agent's lessons and pausing or resuming its learning: EE's policy marks both
// human-only, so an agent's key cannot turn its own learning off or on.
const LEARNING = [
  "POST /workspace/learning/agents/{id}/lessons/{id}/revert",
  "POST /workspace/learning/agents/{id}/pause",
];
const LESSONS = "/workspace/learning/agents/agt-1/lessons";
const REVERT = "/workspace/learning/agents/agt-1/lessons/evt-1/revert";
const PAUSE = "/workspace/learning/agents/agt-1/pause";
const PAUSE_DOC = JSON.stringify({ paused: true });
// Creating, deleting and rotating a workspace credential: a secret's value is set and replaced by a
// person. A key still lists and reads credentials.
const CREDENTIAL_WRITES = [
  "POST /workspace/credentials",
  "DELETE /workspace/credentials/{id}",
  "POST /workspace/credentials/{id}/rotate",
];

// The operations EE's customer contract marks x-omega-human-only, as committed from the contract
// commit and sha256 the file names. OMEGA_EE_CUSTOMER_CONTRACT names EE's own file to check against.
const EE_HUMAN_ONLY = JSON.parse(
  fs.readFileSync(
    new URL("./ee-human-only-operations.json", import.meta.url),
    "utf8",
  ),
);
const HTTP_METHODS = new Set([
  "get",
  "put",
  "post",
  "delete",
  "patch",
  "head",
  "options",
  "trace",
]);
const humanOnlyOf = (contract) =>
  Object.entries(contract.paths).flatMap(([path, item]) =>
    Object.entries(item)
      .filter(
        ([method, operation]) =>
          HTTP_METHODS.has(method) &&
          operation?.["x-omega-human-only"] === true,
      )
      .map(([method]) => `${method.toUpperCase()} ${path}`),
  );
/** A route id without its parameter names: EE says `{credentialId}` where the manifest says `{id}`. */
const shape = (id) => id.replace(/\{[^}]+\}/g, "{}");
/** The person-only operations the gateway routes but does not hold to a session, by manifest id. */
const unheld = (operations) => {
  const routed = new Map(
    ROUTE_MANIFEST.routes.map((route) => [
      shape(`${route.method} ${route.path}`),
      `${route.method} ${route.path}`,
    ]),
  );
  const listed = new Set(SESSION_ONLY_ROUTES.map(shape));
  return operations
    .filter((id) => routed.has(shape(id)) && !listed.has(shape(id)))
    .map((id) => routed.get(shape(id)));
};

test("the table lists exactly the manifest's session-only verbs, and a write route that looks like one cannot be added unlisted", () => {
  const policy = [
    "GET /workspace/tool-policy/workspace",
    "PUT /workspace/tool-policy/workspace",
    "GET /workspace/tool-policy/organization",
    "PUT /workspace/tool-policy/organization",
  ];
  assert.deepEqual(
    [...SESSION_ONLY_ROUTES],
    [
      "POST /workspace/approvals/{id}",
      "PUT /workspace/sandboxes/settings",
      CHANNEL_MINT,
      "PUT /workspace/observe/settings",
      ...policy,
      "PUT /workspace/otlp",
      "POST /workspace/otlp/test",
      ...LEARNING,
      ...CREDENTIAL_WRITES,
    ],
  );
  assert.deepEqual(
    [...lookalikes(ROUTE_MANIFEST.routes), CHANNEL_MINT, ...policy].sort(),
    [...SESSION_ONLY_ROUTES].sort(),
  );
  // The one channel mint the manifest names is the one this table lists (channel-token.test.mjs binds it).
  assert.deepEqual(
    ROUTE_MANIFEST.routes
      .filter((route) => route.credential === "channel-mint")
      .map((route) => `${route.method} ${route.path}`),
    [CHANNEL_MINT],
  );
  // The guard itself catches the routes W2-ACCESS will add, and leaves reads alone.
  assert.deepEqual(
    lookalikes([
      { method: "POST", path: "/workspace/sandboxes/{sandbox}/share-links" },
      { method: "PUT", path: "/workspace/sandboxes/{sandbox}/sharing/{id}" },
      { method: "GET", path: "/workspace/workforce/sharing-candidates" },
      { method: "GET", path: "/workspace/sandboxes/settings" },
      {
        method: "POST",
        path: "/workspace/deployments/{deployment}/access-token",
      },
      {
        method: "POST",
        path: "/workspace/learning/agents/{id}/lessons/{id}/quarantine",
      },
      { method: "GET", path: "/workspace/learning/agents/{id}/lessons" },
      { method: "PATCH", path: "/workspace/credentials/{id}" },
      { method: "GET", path: "/workspace/credentials/{id}/bindings" },
      { method: "POST", path: "/workspace/credentials-report" },
    ]),
    [
      "POST /workspace/sandboxes/{sandbox}/share-links",
      "PUT /workspace/sandboxes/{sandbox}/sharing/{id}",
      "POST /workspace/learning/agents/{id}/lessons/{id}/quarantine",
      "PATCH /workspace/credentials/{id}",
    ],
  );
  // An entry the manifest does not admit stops the gateway at load.
  assert.throws(
    () =>
      checkSessionOnlyRoutes(ROUTE_MANIFEST, ["POST /workspace/approval/{id}"]),
    /names no manifest route/,
  );
  assert.doesNotThrow(() => checkSessionOnlyRoutes());
});

test("the decision fails closed: only auth_method session passes a listed route; unlisted routes are not judged", () => {
  const route = (method, path) => ROUTE_MANIFEST.match(method, path).route;
  const approve = route("POST", APPROVAL);
  assert.equal(decideSessionOnly(approve, { auth_method: "session" }), null);
  for (const claims of [
    { auth_method: "api_key" },
    { auth_method: "Session" },
    { auth_method: undefined },
    {},
    null,
  ]) {
    assert.deepEqual(
      decideSessionOnly(approve, claims),
      { status: 403, ...REFUSED },
      JSON.stringify(claims),
    );
  }
  for (const [method, path] of [
    ["GET", "/workspace/approvals"],
    ["GET", SETTINGS],
    ["GET", OBSERVE_SETTINGS],
    ["POST", "/workspace/sandboxes"],
    ["POST", "/workspace/sandboxes/sb-1/exec"],
  ]) {
    assert.equal(
      decideSessionOnly(route(method, path), { auth_method: "api_key" }),
      null,
      `${method} ${path}`,
    );
  }
});

test("through the server: an API key gets 403 on both verbs and EE sees nothing; a browser session is forwarded", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion()
            : mintAssertion(),
        }),
    },
  });
  const send = (method, path, headers) =>
    fetch(`${base}${path}`, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body:
        method === "GET"
          ? undefined
          : path === SETTINGS
            ? SETTINGS_DOC
            : JSON.stringify({ decision: "approve" }),
    });
  for (const [method, path] of [
    ["POST", APPROVAL],
    ["PUT", SETTINGS],
  ]) {
    const byKey = await send(method, path, KEY);
    assert.equal(byKey.status, 403, `${method} ${path}`);
    assert.deepEqual(
      { ...(await byKey.json()), requestId: undefined },
      { ...REFUSED, requestId: undefined },
    );
  }
  assert.equal(calls.upstream.length, 0, "no key request reached EE");
  assert.equal(
    calls.exchange.length,
    2,
    "each key request was exchanged, then refused on its verified claims",
  );

  // Reads stay open to a key: an agent polls its approval and reads its settings.
  for (const path of ["/workspace/approvals", SETTINGS])
    assert.equal((await send("GET", path, KEY)).status, 200, path);
  assert.equal(calls.upstream.length, 2);
  for (const call of calls.upstream)
    assert.equal(
      claimsOf(call.init.headers.authorization.slice("Bearer ".length))
        .auth_method,
      "api_key",
    );

  // The same verbs by a browser session reach EE with a session assertion.
  for (const [method, path] of [
    ["POST", APPROVAL],
    ["PUT", SETTINGS],
  ]) {
    assert.equal(
      (await send(method, path, { authorization: "Bearer fa-browser-token" }))
        .status,
      200,
      `${method} ${path}`,
    );
  }
  assert.equal(calls.upstream.length, 4);
  for (const call of calls.upstream.slice(2)) {
    const claims = claimsOf(
      call.init.headers.authorization.slice("Bearer ".length),
    );
    assert.equal(claims.auth_method, "session");
    assert.equal(call.init.headers["x-api-key"], undefined);
  }
});

test("an agent's learning: an API key gets 403 on removing a lesson and on pausing learning, and EE sees nothing; the key still reads lessons and a session's writes are forwarded", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion()
            : mintAssertion(),
        }),
    },
  });
  const send = (method, path, headers) =>
    fetch(`${base}${path}`, {
      method,
      headers:
        path === PAUSE
          ? { "content-type": "application/json", ...headers }
          : headers,
      body: path === PAUSE ? PAUSE_DOC : undefined,
    });
  for (const path of [REVERT, PAUSE]) {
    const byKey = await send("POST", path, KEY);
    assert.equal(byKey.status, 403, path);
    assert.deepEqual(
      { ...(await byKey.json()), requestId: undefined },
      { ...REFUSED, requestId: undefined },
      path,
    );
    // A presented assertion that says api_key is refused the same way.
    const byKeyAssertion = await send("POST", path, {
      authorization: `Bearer ${mintKeyAssertion()}`,
    });
    assert.equal(byKeyAssertion.status, 403, path);
    assert.equal((await byKeyAssertion.json()).error, REFUSED.error, path);
  }
  assert.equal(calls.upstream.length, 0, "no key write reached EE");
  assert.equal(
    calls.exchange.length,
    2,
    "each key request was exchanged, then refused on its verified claims",
  );

  // A key still reads the agent's lessons.
  assert.equal((await send("GET", LESSONS, KEY)).status, 200);
  assert.equal(calls.upstream.length, 1);
  assert.equal(
    claimsOf(
      calls.upstream[0].init.headers.authorization.slice("Bearer ".length),
    ).auth_method,
    "api_key",
  );

  // The same two writes by a signed-in person reach EE with a session assertion.
  for (const path of [REVERT, PAUSE]) {
    assert.equal(
      (await send("POST", path, { authorization: "Bearer fa-browser-token" }))
        .status,
      200,
      path,
    );
  }
  assert.deepEqual(
    calls.upstream.map(
      (call) => `${call.init.method} ${new URL(call.url).pathname}`,
    ),
    [`GET ${LESSONS}`, `POST ${REVERT}`, `POST ${PAUSE}`],
  );
  for (const call of calls.upstream.slice(1)) {
    assert.equal(
      claimsOf(call.init.headers.authorization.slice("Bearer ".length))
        .auth_method,
      "session",
    );
    assert.equal(call.init.headers["x-api-key"], undefined);
  }
});

test("a workspace credential: an admin's API key gets 403 on create, delete and rotate, and EE sees nothing; the key still lists credentials and the admin's session is forwarded", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion({ role: "admin" })
            : mintAssertion({ role: "admin" }),
        }),
    },
  });
  const writes = [
    ["POST", "/workspace/credentials", JSON.stringify({ name: "deploy-hook" })],
    ["DELETE", "/workspace/credentials/cred-1", undefined],
    ["POST", "/workspace/credentials/cred-1/rotate", JSON.stringify({})],
  ];
  const send = (method, path, body, headers) =>
    fetch(`${base}${path}`, {
      method,
      body,
      headers:
        body === undefined
          ? headers
          : { "content-type": "application/json", ...headers },
    });
  for (const [method, path, body] of writes) {
    const byKey = await send(method, path, body, KEY);
    assert.equal(byKey.status, 403, `${method} ${path}`);
    assert.deepEqual(
      { ...(await byKey.json()), requestId: undefined },
      { ...REFUSED, requestId: undefined },
      `${method} ${path}`,
    );
    // A presented assertion that says api_key is refused the same way.
    const byKeyAssertion = await send(method, path, body, {
      authorization: `Bearer ${mintKeyAssertion({ role: "admin" })}`,
    });
    assert.equal(byKeyAssertion.status, 403, `${method} ${path}`);
    assert.equal(
      (await byKeyAssertion.json()).error,
      REFUSED.error,
      `${method} ${path}`,
    );
  }
  assert.equal(calls.upstream.length, 0, "no key write reached EE");
  assert.equal(
    calls.exchange.length,
    3,
    "each key request was exchanged, then refused on its verified claims",
  );

  // A key still lists the workspace's credentials.
  assert.equal(
    (await send("GET", "/workspace/credentials", undefined, KEY)).status,
    200,
  );
  assert.equal(calls.upstream.length, 1);
  assert.equal(
    claimsOf(
      calls.upstream[0].init.headers.authorization.slice("Bearer ".length),
    ).auth_method,
    "api_key",
  );

  // The same three writes by a signed-in admin reach EE with a session assertion.
  for (const [method, path, body] of writes) {
    assert.equal(
      (
        await send(method, path, body, {
          authorization: "Bearer fa-browser-token",
        })
      ).status,
      200,
      `${method} ${path}`,
    );
  }
  assert.deepEqual(
    calls.upstream.map(
      (call) => `${call.init.method} ${new URL(call.url).pathname}`,
    ),
    [
      "GET /workspace/credentials",
      ...writes.map(([method, path]) => `${method} ${path}`),
    ],
  );
  for (const call of calls.upstream.slice(1)) {
    assert.equal(
      claimsOf(call.init.headers.authorization.slice("Bearer ".length))
        .auth_method,
      "session",
    );
    assert.equal(call.init.headers["x-api-key"], undefined);
  }
});

test("every operation EE marks for a signed-in person is session-only here: all 14 of the committed list", () => {
  assert.equal(
    EE_HUMAN_ONLY.commit,
    "2b1b8926574f6a421e4d7989a86fe0e3460a8488",
  );
  assert.match(EE_HUMAN_ONLY.sha256, /^[0-9a-f]{64}$/);
  assert.equal(EE_HUMAN_ONLY.operations.length, 14);
  assert.equal(new Set(EE_HUMAN_ONLY.operations).size, 14);
  // Each one names a route the gateway admits, and the gateway holds each one to a session.
  const listed = new Set(SESSION_ONLY_ROUTES.map(shape));
  assert.deepEqual(
    EE_HUMAN_ONLY.operations.filter((id) => !listed.has(shape(id))),
    [],
  );
  assert.deepEqual(unheld(EE_HUMAN_ONLY.operations), []);
  // The check itself finds an operation the gateway routes but does not hold.
  assert.deepEqual(
    unheld([
      "PUT /workspace/sandboxes/settings",
      "GET /workspace/credentials/{credentialId}",
    ]),
    ["GET /workspace/credentials/{id}"],
  );
});

test("the committed person-only list matches EE's customer contract named by OMEGA_EE_CUSTOMER_CONTRACT, and the gateway holds every one it routes", (t) => {
  const file = process.env.OMEGA_EE_CUSTOMER_CONTRACT;
  if (!file) {
    t.skip(
      "set OMEGA_EE_CUSTOMER_CONTRACT to EE's packages/server/src/contracts/customer-openapi.v1.json",
    );
    return;
  }
  const live = humanOnlyOf(JSON.parse(fs.readFileSync(file, "utf8")));
  assert.ok(
    live.length > 0,
    "EE's contract marks no operation x-omega-human-only: wrong file?",
  );
  assert.deepEqual(
    unheld(live),
    [],
    "the gateway routes these person-only operations without holding them to a session",
  );
  assert.deepEqual(
    [...live].sort(),
    [...EE_HUMAN_ONLY.operations].sort(),
    "test/ee-human-only-operations.json is behind EE's contract; copy the list again",
  );
});

// P07 CHAT-TRACE: the Observe content switch is admitted to owners and admins and
// decided by a person. An admin's API key gets 403 on the write and EE sees nothing; the key still reads the setting;
// an admin's browser session changes it; a member's session is refused by the route's admin floor.
test("the Observe settings write: an admin's key gets 403 and EE sees nothing; its session is forwarded; a member is below the floor", async (t) => {
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion({ role: "admin" })
            : init.headers.authorization === "Bearer fa-member-token"
              ? mintAssertion()
              : mintAssertion({ role: "admin" }),
        }),
    },
  });
  const send = (method, headers) =>
    fetch(`${base}${OBSERVE_SETTINGS}`, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body: method === "GET" ? undefined : OBSERVE_DOC,
    });
  const byKey = await send("PUT", KEY);
  assert.equal(byKey.status, 403);
  assert.deepEqual(
    { ...(await byKey.json()), requestId: undefined },
    { ...REFUSED, requestId: undefined },
  );
  assert.equal(calls.upstream.length, 0, "the key's write never reached EE");
  assert.equal((await send("GET", KEY)).status, 200, "a key reads the setting");
  assert.equal(calls.upstream.length, 1);
  assert.equal(
    (await send("PUT", { authorization: "Bearer fa-browser-token" })).status,
    200,
  );
  assert.equal(calls.upstream.length, 2);
  assert.equal(
    claimsOf(
      calls.upstream[1].init.headers.authorization.slice("Bearer ".length),
    ).auth_method,
    "session",
  );
  const member = await send("PUT", { authorization: "Bearer fa-member-token" });
  assert.equal(member.status, 403);
  assert.equal((await member.json()).error, "omega_role_not_permitted");
  assert.equal(calls.upstream.length, 2, "a member's write never reached EE");
});

test("a presented assertion that says api_key is refused too: the decision reads the verified claims, not the header kind", async (t) => {
  const { base, calls } = await startGateway(t, { config: configFor(t) });
  const response = await fetch(`${base}${APPROVAL}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${mintKeyAssertion()}`,
      "content-type": "application/json",
    },
    body: "{}",
  });
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, REFUSED.error);
  assert.equal(calls.upstream.length, 0);
  // A presented session assertion on the same verb is forwarded.
  const allowed = await fetch(`${base}${APPROVAL}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${mintAssertion()}`,
      "content-type": "application/json",
    },
    body: "{}",
  });
  assert.equal(allowed.status, 200);
  assert.equal(calls.upstream.length, 1);
});

test("a refused verb spends no rate token and is answered before its body is read", async (t) => {
  // One write per user: if the key's refusal had spent it, the browser's write would be 429.
  const one = [{ scope: "user", capacity: 1, perSecond: 0.001 }];
  const limiter = createRateLimiter({
    classes: {
      read: one,
      write: one,
      "model-start": one,
      "public-share": [{ scope: "ip", capacity: 1, perSecond: 1 }],
    },
  });
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    rateLimiter: limiter,
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion()
            : mintAssertion(),
        }),
    },
  });
  for (let i = 0; i < 3; i++) {
    const byKey = await fetch(`${base}${SETTINGS}`, {
      method: "PUT",
      headers: { ...KEY, "content-type": "application/json" },
      body: SETTINGS_DOC,
    });
    assert.equal(byKey.status, 403);
  }
  // The same user (the helpers' sub) by browser: still has its one write.
  const byBrowser = await fetch(`${base}${SETTINGS}`, {
    method: "PUT",
    headers: {
      authorization: "Bearer fa-browser-token",
      "content-type": "application/json",
    },
    body: SETTINGS_DOC,
  });
  assert.equal(byBrowser.status, 200);
  assert.equal(calls.upstream.length, 1);

  // A key's body that never ends still gets its 403: nothing waited for the body.
  const port = new URL(base).port;
  const answer = await new Promise((resolve, reject) => {
    const request = http.request(
      {
        host: "127.0.0.1",
        port,
        method: "POST",
        path: APPROVAL,
        headers: {
          ...KEY,
          "content-type": "application/json",
          "transfer-encoding": "chunked",
        },
      },
      (response) => {
        response.resume();
        response.on("end", () => {
          request.destroy();
          resolve(response.statusCode);
        });
      },
    );
    request.on("error", (error) =>
      error.code === "ECONNRESET" ? undefined : reject(error),
    );
    request.write('{"decision":');
  });
  assert.equal(answer, 403);
  assert.equal(calls.upstream.length, 1);
});

test("X-1: a Future AGI API key cannot mint a conversation computer's channel token; a session can, through the gateway", async (t) => {
  const MINT = "/workspace/conversations/c-1/computer/channel";
  const { base, calls } = await startGateway(t, {
    config: configFor(t),
    handler: {
      exchange: (init) =>
        json({
          accessToken: init.headers["x-api-key"]
            ? mintKeyAssertion()
            : mintAssertion(),
        }),
    },
  });
  const mint = (headers) =>
    fetch(`${base}${MINT}`, { method: "POST", headers });
  // By key (exchanged, then refused on its verified `api_key` claims): 403, and EE never sees it.
  const byKey = await mint(KEY);
  assert.equal(byKey.status, 403);
  assert.deepEqual(
    { ...(await byKey.json()), requestId: undefined },
    { ...REFUSED, requestId: undefined },
  );
  // A presented assertion that says api_key is refused the same way.
  const byKeyAssertion = await mint({
    authorization: `Bearer ${mintKeyAssertion()}`,
  });
  assert.equal(byKeyAssertion.status, 403);
  assert.equal(calls.upstream.length, 0, "no key mint reached EE");
  // The Console's path: the person's browser session, exchanged for a session assertion, is forwarded to EE.
  const bySession = await mint({ authorization: "Bearer fa-browser-token" });
  assert.equal(bySession.status, 200);
  assert.equal(calls.upstream.length, 1);
  assert.equal(
    claimsOf(
      calls.upstream[0].init.headers.authorization.slice("Bearer ".length),
    ).auth_method,
    "session",
  );
  // The decision reads the verified claims: a presented session assertion is forwarded too.
  assert.equal(
    (await mint({ authorization: `Bearer ${mintAssertion()}` })).status,
    200,
  );
  assert.equal(calls.upstream.length, 2);
});

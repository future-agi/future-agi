/**
 * Execute the unchanged Axios module in a VM, with real Axios and an in-memory
 * adapter. No frontend startup, browser, refresh endpoint, or network adapter.
 * Run: node --experimental-vm-modules scripts/qa/test_axios_authentication.mjs
 * Optional argument: package.json beside an existing local Axios installation.
 * Auth/storage and contract-validation boundaries are explicit test doubles.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { mock, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createContext, SourceTextModule, SyntheticModule } from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(process.argv[2] || `${root}/frontend/package.json`));
const axios = require("axios");
const sourcePath = `${root}/frontend/src/utils/axios.js`;
const source = readFileSync(sourcePath, "utf8");
const sha = (value) => createHash("sha256").update(value).digest("hex");
const lock = readFileSync(`${root}/frontend/yarn.lock`, "utf8");
assert.equal(axios.VERSION, lock.match(/^axios@\^1\.5\.1:\n  version "([^"]+)"/m)?.[1]);

const access = "synthetic-access";
const rotated = "synthetic-rotated-access";
const originalHref = "http://offline.invalid/observe";
const plain = (value) => JSON.parse(JSON.stringify(value));
const unavailable = {
  status: false,
  type: "service_unavailable",
  code: "service_unavailable",
  detail: "Database temporarily unavailable.",
  message: "Database temporarily unavailable.",
  result: "Database temporarily unavailable.",
};
const denied = {
  status: false,
  type: "permission_error",
  code: "permission_denied",
  detail: "Access denied to this workspace",
};
const invalid = {
  status: false,
  type: "authentication_error",
  code: "authentication_failed",
  detail: "Access token expired",
};

async function fixture(replies, refresh = async () => ({ data: { access: rotated } })) {
  const auth = {
    addToQueue: mock.fn(),
    clearTokens: mock.fn(),
    getIsRefreshing: mock.fn(() => false),
    getRefreshToken: mock.fn(() => "synthetic-refresh"),
    getRememberMe: mock.fn(() => true),
    processQueue: mock.fn(),
    refreshTokenRequest: mock.fn(refresh),
    setIsRefreshing: mock.fn(),
    setSession: mock.fn(),
  };
  const resetUser = mock.fn();
  const window = { location: { href: originalHref }, dispatchEvent: mock.fn() };
  const context = createContext({
    window,
    sessionStorage: {
      getItem: (key) => ({ organizationId: "fixture-org", workspaceId: "fixture-ws" })[key] ?? null,
      setItem: mock.fn(),
    },
    URLSearchParams,
  });
  const contracts = {
    assertContractedRequestConfig: mock.fn((value) => value),
    assertContractedResponse: mock.fn((value) => value),
    findOpenApiEndpoint: () => null,
  };
  const imports = {
    axios: { default: axios },
    notistack: { enqueueSnackbar: () => assert.fail("Unexpected snackbar path") },
    "src/auth/context/jwt/utils": auth,
    "src/config-global": { HOST_API: "http://offline.invalid" },
    "src/api/contracts/api-surface": { apiPath: (path) => path },
    "src/api/contracts/openapi-contract": contracts,
    "./Mixpanel": { resetUser },
    "./logger": { default: { debug() {}, warn() {} } },
  };
  const module = new SourceTextModule(source, { context, identifier: sourcePath });
  await module.link((specifier) => {
    if (["./constants", "./sessionKeys"].includes(specifier)) {
      const path = `${root}/frontend/src/utils/${specifier.slice(2)}.js`;
      return new SourceTextModule(readFileSync(path, "utf8"), { context, identifier: path });
    }
    assert.ok(Object.hasOwn(imports, specifier), "Unexpected application dependency");
    const values = imports[specifier];
    return new SyntheticModule(Object.keys(values), function () {
      for (const [key, value] of Object.entries(values)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  const client = module.namespace.default;
  const requests = [];
  const adapter = async (config) => {
    // Every request/replay must stay on this adapter; replies are consumed once.
    assert.equal(config.adapter, adapter);
    const next = replies[requests.length];
    requests.push(config);
    assert.ok(next, "Unexpected additional request/retry");
    const response = { ...next, config, headers: {} };
    if (next.status >= 400) {
      throw new axios.AxiosError("Synthetic HTTP failure", next.status >= 500 ? "ERR_BAD_RESPONSE" : "ERR_BAD_REQUEST", config, undefined, response);
    }
    return response;
  };
  client.defaults.adapter = adapter;
  const send = (url = "/tracer/users/") => client.request({
    url,
    method: url === "/accounts/user-info/" ? "get" : "post",
    ...(url === "/accounts/user-info/" ? {} : { data: { project_id: "fixture-project" } }),
    headers: { Authorization: `Bearer ${access}`, "X-Organization-Id": "fixture-org", "X-Workspace-Id": "fixture-ws" },
  });
  return { client, send, requests, auth, resetUser, window, contracts };
}

function assertUntouched(f) {
  for (const name of Object.keys(f.auth)) assert.equal(f.auth[name].mock.callCount(), 0, name);
  assert.equal(f.resetUser.mock.callCount(), 0);
  assert.equal(f.window.location.href, originalHref);
  assert.equal(f.requests.length, 1);
}

async function rejected(promise, body, statusCode, transportCode) {
  await assert.rejects(promise, (error) => {
    assert.deepEqual(plain(error), { ...body, statusCode, transportCode });
    return true;
  });
}

for (const url of ["/accounts/user-info/", "/tracer/users/", "/tracer/dashboard/metrics/"]) {
  await test(`503 rejects unchanged without refresh or token clearing: ${url}`, async () => {
    const f = await fixture([{ status: 503, data: unavailable }]);
    await rejected(f.send(url), unavailable, 503, "ERR_BAD_RESPONSE");
    assertUntouched(f);
    assert.equal(f.contracts.assertContractedResponse.mock.callCount(), 1);
  });
}

await test("401 refreshes once and real Axios replays exact body and scopes", async () => {
  const f = await fixture([{ status: 401, data: invalid }, { status: 200, data: { fixture: "replay" } }]);
  const response = await f.send();
  assert.equal(response.status, 200);
  assert.deepEqual(response.data, { fixture: "replay" });
  assert.equal(f.requests.length, 2);
  assert.equal(f.auth.refreshTokenRequest.mock.callCount(), 1);
  assert.equal(f.auth.setSession.mock.callCount(), 1);
  assert.ok(f.auth.setSession.mock.calls[0].arguments[0] === rotated, "Rotated credential handed to session helper");
  assert.equal(f.auth.setSession.mock.calls[0].arguments[1], "fixture-org");
  assert.deepEqual(f.auth.setIsRefreshing.mock.calls.map((c) => c.arguments[0]), [true, false]);
  assert.equal(f.auth.clearTokens.mock.callCount(), 0);
  assert.equal(f.resetUser.mock.callCount(), 0);
  assert.equal(f.window.location.href, originalHref);
  for (const r of f.requests) {
    assert.equal(r.url, "/tracer/users/");
    assert.equal(r.method, "post");
    assert.deepEqual(JSON.parse(r.data), { project_id: "fixture-project" });
    assert.equal(r.headers.get("X-Organization-Id"), "fixture-org");
    assert.equal(r.headers.get("X-Workspace-Id"), "fixture-ws");
  }
  assert.ok(f.requests[1].headers.get("Authorization") === `Bearer ${rotated}`, "Replay uses rotated credential");
  assert.equal(f.requests[1]._retry, true);
  assert.equal(f.client.defaults.headers.common["X-Workspace-Id"], "fixture-ws");
  assert.equal(f.client.defaults.headers.common["X-Organization-Id"], "fixture-org");
});

await test("401 then 503 replay remains an error, not success or another refresh", async () => {
  const f = await fixture([{ status: 401, data: invalid }, { status: 503, data: unavailable }]);
  await rejected(f.send(), unavailable, 503, "ERR_BAD_RESPONSE");
  assert.equal(f.requests.length, 2);
  assert.equal(f.auth.refreshTokenRequest.mock.callCount(), 1);
  assert.equal(f.auth.clearTokens.mock.callCount(), 0);
});

await test("401 then another 401 rejects with no refresh loop", async () => {
  const f = await fixture([{ status: 401, data: invalid }, { status: 401, data: invalid }]);
  await rejected(f.send(), invalid, 401, "ERR_BAD_REQUEST");
  assert.equal(f.requests.length, 2);
  assert.equal(f.auth.refreshTokenRequest.mock.callCount(), 1);
});

await test("failed refresh rejects without replay; existing cleanup is retained", async () => {
  const failure = new Error("Synthetic refresh failure");
  const f = await fixture([{ status: 401, data: invalid }], async () => { throw failure; });
  await assert.rejects(f.send(), (error) => error === failure);
  assert.equal(f.requests.length, 1);
  assert.equal(f.auth.refreshTokenRequest.mock.callCount(), 1);
  assert.equal(f.auth.clearTokens.mock.callCount(), 1);
  assert.equal(f.resetUser.mock.callCount(), 1);
  assert.equal(f.auth.setSession.mock.calls[0].arguments[0], null);
  assert.equal(f.window.location.href, "/auth/jwt/login");
});

for (const url of ["/tracer/users/", "/tracer/dashboard/metrics/"]) {
  await test(`nonauth 403 rejects without refresh or token clearing: ${url}`, async () => {
    const f = await fixture([{ status: 403, data: denied }]);
    await rejected(f.send(url), denied, 403, "ERR_BAD_REQUEST");
    assertUntouched(f);
  });
}

await test("auth-endpoint 403 does not refresh but retains existing logout behavior", async () => {
  const f = await fixture([{ status: 403, data: denied }]);
  await rejected(f.send("/accounts/user-info/"), denied, 403, "ERR_BAD_REQUEST");
  assert.equal(f.requests.length, 1);
  assert.equal(f.auth.refreshTokenRequest.mock.callCount(), 0);
  assert.equal(f.auth.clearTokens.mock.callCount(), 1);
  assert.equal(f.window.location.href, "/auth/jwt/login");
});

console.log(JSON.stringify({ axios: axios.VERSION, sourceSHA256: sha(source), network: "in-memory adapter only", scope: "actual interceptor; auth/storage/contracts mocked" }));

#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// The site the console-frame e2e drives. It builds the real Future AGI
// frontend once, with only the router module swapped for a test router that
// mounts the real ConsoleFramePage at /dashboard/console/*, and serves it the
// way production does: the Console is on the same site under /omega/console/.
//
//   fake host  http://localhost:3061  /omega/console/ is fake-console.cjs
//   EE host    http://localhost:3062  /omega/console/ is the EE Console's
//              embed build (CONSOLE_E2E_EE_DIST), /omega/api/* a customer-only
//              fixture gateway (CONSOLE_E2E_EE_FIXTURES, the EE lane's
//              e2e/embed/fixtures.ts). Started only when CONSOLE_E2E_EE_DIST is set.
//
// Both hosts answer POST /accounts/omega/session-exchange/ like Future AGI's
// backend: fresh omega_delegate_ assertions per call, `count` of them when the
// body asks (CP P7), or a 403/503 when a test says so (403
// omega_viewer_not_admitted for a viewer). Tests read what happened from
// /__e2e/state. Each host also takes the build's Sentry envelopes
// (sentry-sink.cjs) and serves a runtime config a test can change.
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { fakeConsoleHtml } = require("./fake-console.cjs");
const { handleSentry } = require("./sentry-sink.cjs");
const { startCrossOrigin } = require("./xo-hosts.cjs");
const { DIST, build } = require("./build-harness.cjs");

const FAKE_PORT = Number(process.env.CONSOLE_E2E_FAKE_PORT || 3061);
const EE_PORT = Number(process.env.CONSOLE_E2E_EE_PORT || 3062);
const EE_DIST = process.env.CONSOLE_E2E_EE_DIST || "";
const EE_FIXTURES = process.env.CONSOLE_E2E_EE_FIXTURES || "";
// The EE Console packaged for its own origin (CP P6), built to trust the
// cross-origin host below as its parent: starts the two-origin topology.
const XO_ORIGIN_DIR = process.env.CONSOLE_E2E_XO_ORIGIN_DIR || "";
const XO_HOST_PORT = Number(process.env.CONSOLE_E2E_XO_HOST_PORT || 43310);
// The most keys one exchange call mints (accounts/omega_session.py MAX_BATCH).
const MAX_BATCH = 8;

const CONSOLE_BASE = "/omega/console/";
const EXCHANGE_PATH = "/accounts/omega/session-exchange/";
const CAPABILITIES_PATH = "/api/capabilities/";
// GET /api/capabilities/ in the server's wire shape (tfc/capabilities/
// contracts.py, the frontend's response contract), every Omega product
// admitted.
const CAPABILITIES = {
  deployment_flavor: "oss_image",
  display_mode: "oss",
  license_state: "missing",
  features: Object.fromEntries(
    ["console", "sandboxes", "deployments", "studio_tab", "hub"].map((p) => [
      `omega_${p}`,
      {
        display_name: p,
        allowed: true,
        reason_code: null,
        requires_network: false,
        oss_baseline: false,
      },
    ]),
  ),
};
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".mp3": "audio/mpeg",
  ".wasm": "application/wasm",
};

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");

function send(res, status, type, body, headers = {}) {
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    ...headers,
  });
  res.end(body);
}

const sendJson = (res, status, body) =>
  send(res, status, "application/json", JSON.stringify(body));

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!chunks.length) return resolve(undefined);
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        resolve(undefined);
      }
    });
  });
}

function serveFile(res, root, relative) {
  const file = path.normalize(path.join(root, relative));
  if (!file.startsWith(root) || !fs.existsSync(file)) return false;
  if (!fs.statSync(file).isFile()) return false;
  send(
    res,
    200,
    MIME[path.extname(file)] || "application/octet-stream",
    fs.readFileSync(file),
  );
  return true;
}

function createHost({
  name,
  port,
  consoleDist,
  fixtures,
  bind = "localhost",
  baseConfig = {},
}) {
  const state = {
    exchangeMode: "ok",
    consoleUp: true,
    exchanges: [],
    minted: new Set(),
    api: [],
    tenant: null,
    sentry: [],
    config: {},
    want: null,
  };
  const reset = () => {
    state.exchangeMode = "ok";
    state.consoleUp = true;
    state.exchanges = [];
    state.minted = new Set();
    state.api = [];
    state.sentry = [];
    state.config = {};
    state.want = null;
    state.tenant = fixtures
      ? fixtures.createTenants().values().next().value
      : null;
  };
  reset();

  async function exchange(req, res) {
    const body = await readBody(req);
    const url = new URL(req.url, "http://host.invalid");
    const count = body?.count;
    state.exchanges.push({
      audience: body?.audience ?? null,
      count: count ?? null,
      query: url.search,
      mode: state.exchangeMode,
      at: Date.now(),
    });
    if (state.exchangeMode === "hold") {
      // Keeps the page on its connecting state for a screenshot.
      await new Promise((resolve) => setTimeout(resolve, 30_000));
    }
    if (state.exchangeMode === "refused") {
      return sendJson(res, 403, { code: "omega_role_refused" });
    }
    if (state.exchangeMode === "viewer") {
      // Django's envelope for a viewer (accounts/views/omega_session.py).
      const message =
        "Viewers cannot open Omega. Ask a workspace admin for member access.";
      return sendJson(res, 403, {
        status: false,
        type: "permission_error",
        code: "omega_viewer_not_admitted",
        detail: message,
        message,
        error: message,
        result: message,
      });
    }
    if (state.exchangeMode === "unavailable") {
      return sendJson(res, 503, { code: "omega_delegation_unconfigured" });
    }
    if (
      count !== undefined &&
      (!Number.isInteger(count) || count < 1 || count > MAX_BATCH)
    ) {
      return sendJson(res, 400, { code: "invalid_request" });
    }
    const tokens = Array.from(
      { length: count ?? 1 },
      () => `omega_delegate_${crypto.randomBytes(24).toString("base64url")}`,
    );
    tokens.forEach((token) => state.minted.add(token));
    return sendJson(res, 200, {
      tokenType: "Bearer",
      accessToken: tokens[0],
      expiresIn: 45,
      ...(count !== undefined ? { accessTokens: tokens } : {}),
    });
  }

  async function gateway(req, res, url) {
    const apiPath = url.pathname.slice("/omega/api".length) + url.search;
    const bearer = /^Bearer (.+)$/.exec(req.headers.authorization || "")?.[1];
    state.api.push({
      method: req.method,
      path: apiPath,
      keyHash: bearer ? sha256(bearer) : null,
    });
    if (!apiPath.startsWith("/workspace")) {
      return sendJson(res, 403, { error: "route_not_allowed" });
    }
    if (!bearer || !state.minted.has(bearer)) {
      return sendJson(res, 401, {
        schemaVersion: "omega-workspace/v1",
        error: { code: "unauthenticated", message: "Sign in again." },
      });
    }
    if (!fixtures) return sendJson(res, 404, { error: "no_fixtures" });
    const body =
      req.method === "POST" || req.method === "PATCH"
        ? await readBody(req)
        : undefined;
    const result = fixtures.handleWorkspace(
      state.tenant,
      req.method,
      apiPath,
      body,
    );
    return sendJson(res, result.status, result.body);
  }

  function consolePage(res, url) {
    if (!state.consoleUp) return send(res, 404, "text/plain", "Not found");
    const base =
      url.pathname === CONSOLE_BASE ||
      url.pathname === CONSOLE_BASE.slice(0, -1);
    if (!consoleDist) {
      return base
        ? send(
            res,
            200,
            "text/html; charset=utf-8",
            fakeConsoleHtml({ want: state.want }),
          )
        : send(res, 404, "text/plain", "Not found");
    }
    const relative = base
      ? "console.html"
      : url.pathname.slice(CONSOLE_BASE.length);
    if (!serveFile(res, consoleDist, relative)) {
      send(res, 404, "text/plain", "Not found");
    }
    return undefined;
  }

  async function control(req, res, url) {
    if (url.pathname === "/__e2e/state") {
      return sendJson(res, 200, {
        name,
        exchangeMode: state.exchangeMode,
        consoleUp: state.consoleUp,
        exchanges: state.exchanges,
        // The keys are fake and local; tests look for them in URLs and storage.
        minted: [...state.minted],
        mintedHashes: [...state.minted].map(sha256),
        api: state.api,
        sentry: state.sentry,
      });
    }
    if (url.pathname === "/__e2e/reset" && req.method === "POST") {
      reset();
      return sendJson(res, 200, { ok: true });
    }
    if (url.pathname === "/__e2e/mode" && req.method === "POST") {
      const body = (await readBody(req)) || {};
      if (body.exchange) state.exchangeMode = body.exchange;
      if (typeof body.consoleUp === "boolean") state.consoleUp = body.consoleUp;
      if (body.config && typeof body.config === "object") {
        state.config = body.config;
      }
      if ("want" in body) state.want = body.want;
      return sendJson(res, 200, { ok: true });
    }
    return sendJson(res, 404, { error: "not_found" });
  }

  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", `http://localhost:${port}`);
      if (url.pathname.startsWith("/__e2e/"))
        return await control(req, res, url);
      if (handleSentry(req, res, url, state.sentry)) return undefined;
      // The runtime config the docker entrypoint writes: the API is this site.
      if (url.pathname === "/config.js") {
        const config = { ...baseConfig, ...state.config };
        return send(
          res,
          200,
          "text/javascript; charset=utf-8",
          `window.__FUTURE_AGI_CONFIG__ = Object.assign({ VITE_HOST_API: window.location.origin }, ${JSON.stringify(config)});\n`,
        );
      }
      if (url.pathname === EXCHANGE_PATH && req.method === "POST") {
        return await exchange(req, res);
      }
      // The controlled rollout's answer (READINESS 3.13): every Omega
      // product admitted, so the product switcher offers the Console.
      if (url.pathname === CAPABILITIES_PATH && req.method === "GET") {
        return sendJson(res, 200, CAPABILITIES);
      }
      if (url.pathname.startsWith("/omega/api/"))
        return await gateway(req, res, url);
      if (
        url.pathname === CONSOLE_BASE.slice(0, -1) ||
        url.pathname.startsWith(CONSOLE_BASE)
      ) {
        return consolePage(res, url);
      }
      if (req.method === "GET" && serveFile(res, DIST, url.pathname))
        return undefined;
      const wantsHtml = (req.headers.accept || "").includes("text/html");
      if (req.method === "GET" && wantsHtml) {
        return serveFile(res, DIST, "index.html");
      }
      // Any other API call the app makes on start-up: nothing here.
      return sendJson(res, 404, { error: "not_found" });
    } catch (error) {
      console.error(`[console-host] ${name}:`, error);
      return sendJson(res, 500, { error: "harness_error" });
    }
  });
  return new Promise((resolve) => {
    server.listen(port, bind, () => {
      console.log(`[console-host] ${name} on http://${bind}:${port}`);
      resolve({ server, state });
    });
  });
}

async function loadFixtures() {
  if (!EE_FIXTURES) return null;
  const module = await import(pathToFileURL(path.resolve(EE_FIXTURES)).href);
  return {
    createTenants: module.createTenants,
    handleWorkspace: module.handleWorkspace,
  };
}

async function main() {
  await build();
  // The fake host starts last: Playwright waits on its state URL, so every
  // other server is already listening when the first test runs.
  const hosts = [];
  const fixtures = await loadFixtures();
  if (EE_DIST) {
    hosts.push(
      await createHost({
        name: "ee",
        port: EE_PORT,
        consoleDist: path.resolve(EE_DIST),
        fixtures,
      }),
    );
  }
  const others = [];
  if (XO_ORIGIN_DIR) {
    const xo = await startCrossOrigin({
      hostPort: XO_HOST_PORT,
      originDir: path.resolve(XO_ORIGIN_DIR),
      fixtures,
      createHost,
    });
    hosts.push(xo.host);
    others.push(...xo.servers);
  }
  hosts.push(await createHost({ name: "fake", port: FAKE_PORT }));
  const stop = () => {
    hosts.forEach(({ server }) => server.close());
    others.forEach((server) => server.close());
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

// The Console-origin listener (CP P6, D1, SEC3, SEC5; READINESS L05 Console path).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import {
  consolePolicies,
  CSP_REPORT_PATH,
  resolveStaticFile,
} from "../src/console-origin.mjs";
import { loadConfig } from "../src/policy.mjs";
import { mintAssertion, startGateway, writeJwks } from "./helpers.mjs";

const FA = "http://localhost:3031";
const CONSOLE = "http://127.0.0.1:3032";

function consoleBuild(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "omega-console-build-"));
  const outside = fs.mkdtempSync(
    path.join(os.tmpdir(), "omega-console-outside-"),
  );
  t.after(() => {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  });
  // Match edge-console.Dockerfile: the fallback document is at the root, while the
  // built Console (OMEGA_CONSOLE_BASE=/omega/console/) is mounted below that prefix.
  const consoleDir = path.join(root, "omega", "console");
  fs.mkdirSync(path.join(consoleDir, "assets"), { recursive: true });
  const document =
    "<!doctype html><title>Console</title><script type=module src=/omega/console/assets/app-Ab12Cd34.js></script>";
  fs.writeFileSync(path.join(root, "index.html"), document);
  fs.writeFileSync(path.join(consoleDir, "console.html"), document);
  fs.writeFileSync(
    path.join(consoleDir, "assets", "app-Ab12Cd34.js"),
    "console.log(1)",
  );
  fs.writeFileSync(
    path.join(consoleDir, "assets", "naïve plan.txt"),
    "encoded asset",
  );
  fs.writeFileSync(
    path.join(consoleDir, "assets", "100% ready.txt"),
    "encoded asset",
  );
  fs.writeFileSync(
    path.join(root, "robots.txt"),
    "User-agent: *\nDisallow: /\n",
  );
  // J00.L3 ruling d: a dotfile that EXISTS in the build and a real asset subdirectory must
  // still 404 (never served, never the SPA document).
  fs.writeFileSync(path.join(consoleDir, ".env"), "NOT_A_SECRET=fixture\n");
  fs.mkdirSync(path.join(consoleDir, ".well-known"), { recursive: true });
  fs.writeFileSync(
    path.join(consoleDir, ".well-known", "probe.txt"),
    "dot dir",
  );
  // The real hosted Console build ships one dot-leading file: Vite writes .vite/manifest.json
  // when the base is not "/" (read only from disk at build time, never by the browser).
  fs.mkdirSync(path.join(consoleDir, ".vite"), { recursive: true });
  fs.writeFileSync(path.join(consoleDir, ".vite", "manifest.json"), "{}");
  fs.mkdirSync(path.join(consoleDir, "assets", "nested"), { recursive: true });
  fs.writeFileSync(path.join(consoleDir, "assets", "nested", "x.js"), "1");
  fs.writeFileSync(path.join(outside, "secret.txt"), "outside the build");
  fs.symlinkSync(
    path.join(outside, "secret.txt"),
    path.join(consoleDir, "leak.txt"),
  );
  return root;
}

async function consoleGateway(t, extra = {}) {
  const config = loadConfig({
    OMEGA_GATEWAY_MODE: "local",
    OMEGA_REMOTE_BASE_URL: "http://127.0.0.1:4318",
    OMEGA_SESSION_EXCHANGE_URL:
      "http://127.0.0.1:8000/accounts/omega/session-exchange/",
    OMEGA_ALLOWED_ORIGINS: FA,
    OMEGA_GATEWAY_BASE_PATH: "/omega/api",
    OMEGA_ASSERTION_JWKS_FILE: writeJwks(t),
    OMEGA_CONSOLE_ORIGIN: CONSOLE,
    OMEGA_CONSOLE_STATIC_DIR: consoleBuild(t),
    ...extra,
  });
  const lines = [];
  const started = await startGateway(t, {
    config,
    log: (line) => lines.push(line),
  });
  return { ...started, lines };
}

test("Console documents carry the strict CSP framed only by Future AGI, and the security headers", async (t) => {
  const { consoleBase } = await consoleGateway(t);
  for (const route of [
    "/omega/console",
    "/omega/console/",
    "/omega/console/agents/agent-1",
    "/omega/console/console.html",
  ]) {
    const response = await fetch(`${consoleBase}${route}`, {
      headers: { accept: "text/html" },
    });
    assert.equal(response.status, 200, route);
    assert.match(await response.text(), /<title>Console<\/title>/);
    const csp = response.headers.get("content-security-policy");
    for (const directive of [
      "default-src 'self'",
      "script-src 'self'",
      "object-src 'none'",
      "base-uri 'none'",
      `frame-ancestors ${FA}`,
      "connect-src 'self'",
      `report-uri ${CSP_REPORT_PATH}`,
    ]) {
      assert.ok(
        csp.split("; ").includes(directive),
        `${route}: ${directive} missing from ${csp}`,
      );
    }
    assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|\*/);
    assert.equal(
      response.headers.get("content-security-policy-report-only"),
      `require-trusted-types-for 'script'; report-uri ${CSP_REPORT_PATH}`,
    );
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
    assert.equal(response.headers.get("referrer-policy"), "no-referrer");
    assert.equal(response.headers.get("cache-control"), "no-cache");
    assert.equal(response.headers.get("access-control-allow-origin"), null);
  }
  const asset = await fetch(
    `${consoleBase}/omega/console/assets/app-Ab12Cd34.js`,
  );
  assert.equal(asset.status, 200);
  assert.equal(
    asset.headers.get("content-type"),
    "text/javascript; charset=utf-8",
  );
  assert.equal(
    asset.headers.get("cache-control"),
    "public, max-age=31536000, immutable",
  );
  assert.equal(await asset.text(), "console.log(1)");
  assert.equal(
    (await fetch(`${consoleBase}/omega/console/assets/missing.js`)).status,
    404,
  );
});

/** Keep dot segments and encodings intact; fetch would normalize some before sending. */
function rawRequest(base, method, target, accept = "*/*") {
  return new Promise((resolve, reject) => {
    const request = http.request(
      base,
      { method, path: target, headers: { accept } },
      (response) => {
        const chunks = [];
        response.on("error", reject);
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            headers: response.headers,
            body: Buffer.concat(chunks).toString("utf8"),
          }),
        );
      },
    );
    request.on("error", reject);
    request.end();
  });
}

const absentStaticPaths = [
  "/",
  "/omega",
  "/omega/",
  "/omega/foo",
  "/omega/foo/nested",
  "/agents/agent-1",
  "/omega/consolex",
  "/omega/consolex/agents",
  "/omega/console-old",
  "/omega/console.html",
  "/omega/api-ish",
  "/omega/apix",
  "/omega/apix/workspace/capabilities",
  "/omega/api.json",
  "/omega/api%2fworkspace/capabilities",
  "/omega/%61pi/workspace/capabilities",
  "/index.html",
  "/robots.txt",
  "/assets/missing.js",
  "/omega/foo?view=agents",
];
const malformedStaticPaths = [
  "/omega/console/.",
  "/omega/console/..",
  "/omega/console/../foo",
  "/omega/console/../../index.html",
  "/omega/console/agents/../console.html",
  "/omega/console/./agents",
  "/omega/./console/agents",
  "/omega/foo/../console/agents",
  "/omega/console/%2e/agents",
  "/omega/console/%2e%2e/foo",
  "/omega/console/.%2E/foo",
  "/omega/console/%2e./foo",
  "/omega/console/assets/..%2f..%2findex.html",
  "/omega/console/%2F..%2F..%2Findex.html",
  "/omega/console/assets%5c..%5cindex.html",
  "/omega/console/agents\\..\\console.html",
  "/omega/console/%252e%252e%252ffoo",
  "/omega/console/%25252e%25252e%25252ffoo",
  "/omega/console%2fagents",
  "/omega%2fconsole/agents",
  "/omega/%63onsole/agents",
  "/omega/console/%00",
  "/omega/console/%E0%A4%A",
  "/omega/console/%",
  "//omega/console/agents",
  "/omega//console/agents",
  "/omega/console//agents",
];

// J00.L3 ruling d (omega-console P1, 10-31): dotfiles and the asset root are files-only.
// No path segment starting with "." is ever served or answered with the document, and a
// directory or missing file at or below /omega/console/assets is a 404, never the SPA.
const filesOnlyStaticPaths = [
  "/omega/console/.env",
  "/omega/console/.env.local",
  "/omega/console/.git/config",
  "/omega/console/.well-known/probe.txt",
  "/omega/console/.well-known/",
  "/omega/console/.vite/manifest.json",
  "/omega/console/agents/.hidden",
  "/omega/console/assets/.env",
  "/omega/console/%2Eenv",
  "/omega/console/assets",
  "/omega/console/assets/",
  "/omega/console/assets/nested",
  "/omega/console/assets/nested/",
  "/omega/console/assets/no-extension",
  "/omega/console/assets/missing.js",
  // Encoded spellings of the same targets (unreserved-character escapes are otherwise allowed).
  "/omega/console/%61ssets",
  "/omega/console/%61ssets/",
  "/omega/console/%61ssets/nested/",
  "/omega/console/assets/%2Eenv",
  "/omega/console/%2E%67it/config",
];

for (const method of ["GET", "HEAD"]) {
  for (const [kind, routes] of [
    ["outside /omega/console", absentStaticPaths],
    ["with traversal or ambiguous encodings", malformedStaticPaths],
    ["for dotfiles or the files-only asset root", filesOnlyStaticPaths],
  ]) {
    test(`Console ${method} requests ${kind} return 404, never the SPA`, async (t) => {
      const { consoleBase, calls } = await consoleGateway(t);
      const failures = [];
      for (const route of routes) {
        for (const accept of ["*/*", "text/html"]) {
          const response = await rawRequest(consoleBase, method, route, accept);
          if (response.status !== 404)
            failures.push({ route, accept, status: response.status });
          else {
            assert.equal(
              response.headers["content-type"],
              "application/json; charset=utf-8",
              route,
            );
            assert.equal(response.headers["cache-control"], "no-store", route);
            assert.equal(
              response.headers["x-content-type-options"],
              "nosniff",
              route,
            );
            if (method === "HEAD") assert.equal(response.body, "", route);
            else {
              // The listener's one 404 contract: exactly {error, requestId}; requestId is the
              // per-request trace id (never a file body, a listing or build information).
              const body = JSON.parse(response.body);
              assert.deepEqual(
                Object.keys(body).sort(),
                ["error", "requestId"],
                route,
              );
              assert.equal(body.error, "not_found", route);
              assert.match(body.requestId, /^[0-9a-f]{32}$/, route);
            }
          }
        }
      }
      assert.deepEqual(calls.exchange, []);
      assert.deepEqual(calls.upstream, []);
      assert.deepEqual(
        failures,
        [],
        "out-of-scope and malformed targets must not serve static content",
      );
    });
  }

  test(`Console ${method} client routes still load the root index under /omega/console`, async (t) => {
    const { consoleBase, gateway } = await consoleGateway(t);
    const document = fs.readFileSync(
      path.join(gateway.config.consoleOrigin.staticDir, "index.html"),
      "utf8",
    );
    for (const route of [
      "/omega/console",
      "/omega/console/",
      "/omega/console/agents/agent-1",
      "/omega/console/agents/agent-1/runs?view=recent",
    ]) {
      for (const accept of ["*/*", "text/html"]) {
        const response = await rawRequest(consoleBase, method, route, accept);
        assert.equal(response.status, 200, route);
        assert.equal(
          response.headers["content-type"],
          "text/html; charset=utf-8",
          route,
        );
        assert.equal(
          response.headers["content-length"],
          String(Buffer.byteLength(document)),
          route,
        );
        assert.equal(response.headers["cache-control"], "no-cache", route);
        assert.match(
          response.headers["content-security-policy"],
          /default-src 'self'/,
          route,
        );
        assert.equal(response.body, method === "HEAD" ? "" : document, route);
      }
    }
    // An explicit document request may include dots in a client route.
    const dotted = await rawRequest(
      consoleBase,
      method,
      "/omega/console/agents/agent.v2",
      "text/html",
    );
    assert.equal(dotted.status, 200);
    assert.equal(dotted.body, method === "HEAD" ? "" : document);
  });

  test(`Console ${method} assets keep their bytes, cache headers and missing-file 404s`, async (t) => {
    const { consoleBase } = await consoleGateway(t);
    // Safe filename encodings must keep working, including unreserved characters.
    for (const route of [
      "/omega/console/assets/app-Ab12Cd34.js",
      "/omega/console/assets/%61pp-Ab12Cd34%2Ejs",
    ]) {
      const asset = await rawRequest(consoleBase, method, route);
      assert.equal(asset.status, 200, route);
      assert.equal(
        asset.headers["content-type"],
        "text/javascript; charset=utf-8",
      );
      assert.equal(
        asset.headers["content-length"],
        String(Buffer.byteLength("console.log(1)")),
      );
      assert.equal(
        asset.headers["cache-control"],
        "public, max-age=31536000, immutable",
      );
      assert.equal(asset.headers["content-security-policy"], undefined);
      assert.equal(asset.body, method === "HEAD" ? "" : "console.log(1)");
    }
    for (const route of [
      "/omega/console/assets/na%C3%AFve%20plan.txt",
      "/omega/console/assets/100%25%20ready.txt",
    ]) {
      const encoded = await rawRequest(consoleBase, method, route);
      assert.equal(encoded.status, 200, route);
      assert.equal(encoded.headers["cache-control"], "no-cache");
      assert.equal(encoded.body, method === "HEAD" ? "" : "encoded asset");
    }
    for (const route of [
      "/omega/console/assets/missing.js",
      "/omega/console/leak.txt",
    ]) {
      assert.equal(
        (await rawRequest(consoleBase, method, route)).status,
        404,
        route,
      );
    }
    const api = await rawRequest(
      consoleBase,
      method,
      "/omega/api",
      "text/html",
    );
    assert.equal(
      api.status,
      403,
      "the API root is routed to the API wall, never to static fallback",
    );
    assert.equal(
      api.headers["content-type"],
      "application/json; charset=utf-8",
    );
  });
}

test("Trusted Types move into the enforced policy when asked", () => {
  const policies = consolePolicies(
    { frameAncestors: [FA] },
    { trustedTypes: "enforce" },
  );
  assert.match(policies.enforced, /require-trusted-types-for 'script'/);
  assert.equal(policies.reportOnly, null);
});

test("static paths cannot leave the build: traversal, encoded dots and symlinks are refused", async (t) => {
  const { consoleBase } = await consoleGateway(t);
  for (const route of [
    "/../../etc/passwd",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/omega/console/leak.txt",
    "/omega/console/assets/..%2f..%2fleak.txt",
    "/%00",
  ]) {
    const response = await fetch(`${consoleBase}${route}`);
    const text = await response.text();
    assert.equal(text.includes("outside the build"), false, route);
    assert.equal(text.includes("root:"), false, route);
  }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "omega-console-resolve-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "index.html"), "x");
  assert.equal(resolveStaticFile(root, "/../index.html"), null);
  assert.equal(resolveStaticFile(root, "/a%5C..%5Cindex.html"), null);
  assert.equal(resolveStaticFile(root, "/%E0%A4%A"), null);
  assert.ok(resolveStaticFile(root, "/"));
});

test("the Console-origin API accepts pre-minted assertions only, never a Future AGI token", async (t) => {
  const { consoleBase, calls } = await consoleGateway(t);
  const browser = await fetch(
    `${consoleBase}/omega/api/workspace/capabilities`,
    { headers: { authorization: "Bearer fi-browser-token" } },
  );
  assert.equal(browser.status, 401);
  assert.equal((await browser.json()).error, "omega_assertion_required");
  assert.equal(browser.headers.get("x-content-type-options"), "nosniff");
  assert.equal(
    calls.exchange.length,
    0,
    "a browser token never reaches the issuer from this origin",
  );
  const assertion = mintAssertion({ iss: "https://host.example.test/omega" });
  const ok = await fetch(`${consoleBase}/omega/api/workspace/capabilities`, {
    headers: { authorization: `Bearer ${assertion}`, origin: CONSOLE },
  });
  // EE is not running in this test: the call reaches the stub upstream through the harness.
  assert.equal(ok.status, 200);
  assert.equal(calls.upstream.length, 1);
  assert.equal(
    calls.upstream[0].init.headers.authorization,
    `Bearer ${assertion}`,
  );
  const foreign = await fetch(
    `${consoleBase}/omega/api/workspace/capabilities`,
    {
      headers: {
        authorization: `Bearer ${mintAssertion()}`,
        origin: "https://evil.example",
      },
    },
  );
  assert.equal(foreign.status, 403);
  assert.equal((await foreign.json()).error, "origin_not_allowed");
  const faOrigin = await fetch(
    `${consoleBase}/omega/api/workspace/capabilities`,
    { headers: { authorization: `Bearer ${mintAssertion()}`, origin: FA } },
  );
  assert.equal(
    faOrigin.status,
    403,
    "the Future AGI origin does not call the Console origin's API",
  );
  const operator = await fetch(`${consoleBase}/omega/api/os/agents`, {
    headers: { authorization: `Bearer ${mintAssertion({ role: "owner" })}` },
  });
  assert.equal(operator.status, 403);
  assert.equal(calls.upstream.length, 1);
});

test("the app listener keeps serving the tabs with browser tokens while the Console listener runs", async (t) => {
  const { base, calls } = await consoleGateway(t);
  const response = await fetch(`${base}/omega/api/workspace/sandboxes`, {
    headers: { authorization: "Bearer fi-browser-token", origin: FA },
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("access-control-allow-origin"), FA);
  assert.equal(calls.exchange.length, 1);
  // The app listener serves no Console files or SPA fallback, on either method.
  for (const method of ["GET", "HEAD"]) {
    for (const route of [
      "/",
      "/omega",
      "/omega/foo",
      "/omega/console",
      "/omega/console/agents/agent-1",
      "/omega/console/assets/app-Ab12Cd34.js",
      "/index.html",
    ]) {
      assert.equal(
        (await rawRequest(base, method, route, "text/html")).status,
        404,
        `${method} ${route}`,
      );
    }
  }
});

test("CSP reports are accepted, reduced to directive and blocked origin, and logged", async (t) => {
  const { consoleBase, lines } = await consoleGateway(t);
  const response = await fetch(`${consoleBase}${CSP_REPORT_PATH}`, {
    method: "POST",
    headers: { "content-type": "application/csp-report" },
    body: JSON.stringify({
      "csp-report": {
        "violated-directive": "script-src-elem",
        "blocked-uri": "https://evil.example/x.js?token=abc",
        "document-uri": `${CONSOLE}/agents/secret-agent`,
      },
    }),
  });
  assert.equal(response.status, 204);
  await new Promise((resolve) => setTimeout(resolve, 20));
  const line = lines.find((record) => record.route === CSP_REPORT_PATH);
  assert.equal(line.reason, "csp:script-src-elem:https://evil.example:enforce");
  assert.equal(JSON.stringify(lines).includes("secret-agent"), false);
  assert.equal(JSON.stringify(lines).includes("token=abc"), false);
  const oversized = await fetch(`${consoleBase}${CSP_REPORT_PATH}`, {
    method: "POST",
    body: "x".repeat(20_000),
  });
  assert.equal(oversized.status, 204);
});

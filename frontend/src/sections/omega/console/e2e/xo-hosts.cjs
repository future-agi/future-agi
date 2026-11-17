/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// The two-origin topology of CP decision D1 for the console-frame e2e, with
// the real pieces on both sides:
//
//   host     http://127.0.0.1:<p>    the real Future AGI build (ConsoleFramePage),
//                                    whose runtime config points the frame at
//   console  http://127.0.0.1:<p+1>  the EE Console packaged for its own origin,
//                                    served by Future AGI's own Console-origin
//                                    listener code (services/omega-gateway/src/
//                                    console-origin.mjs: serveStatic, its CSP with
//                                    frame-ancestors <host>, its security headers,
//                                    its CSP report reader), plus a customer-only
//                                    fixture API at /omega/api/* that takes only
//                                    keys the host minted, each once
//   foreign  http://127.0.0.1:<p+2>  a site that is neither: it tries to frame the
//                                    Console, and offers a page that says hello
//
// Same host, different ports: same site, different origins, as in production.
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const LISTENER = path.resolve(
  __dirname,
  "../../../../../../services/omega-gateway/src/console-origin.mjs",
);

const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const requestId = () => `req_${crypto.randomBytes(10).toString("hex")}`;

function readText(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, {
    "content-type": "application/json",
    "cache-control": "no-store",
    ...headers,
  });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
};

function foreignPages(consoleOrigin) {
  const frameConsole = `<!doctype html><html><head><meta charset="utf-8"><title>Elsewhere</title></head>
<body><p>A site that is not Future AGI.</p>
<iframe id="console" title="Console" src="${consoleOrigin}/omega/console/?embed=1" style="width:800px;height:500px"></iframe>
<script>
window.__messages = [];
window.addEventListener("message", function (event) {
  window.__messages.push({ origin: event.origin, type: event.data && event.data.type });
});
</script></body></html>`;
  const hello = `<!doctype html><html><head><meta charset="utf-8"><title>Hello</title></head>
<body><p>A frame from elsewhere.</p><script>
window.__received = [];
window.addEventListener("message", function (event) {
  window.__received.push({ origin: event.origin, type: event.data && event.data.type });
});
var n = 0;
var timer = setInterval(function () {
  n += 1;
  window.parent.postMessage({ type: "embed.hello", protocol: "omega-embed/1", want: 4 }, "*");
  if (n >= 10) clearInterval(timer);
}, 200);
</script></body></html>`;
  return { "/frame-console.html": frameConsole, "/hello.html": hello };
}

const listen = (server, port) =>
  new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve(server));
  });

async function startCrossOrigin({ hostPort, originDir, fixtures, createHost }) {
  const HOST = `http://127.0.0.1:${hostPort}`;
  const CONSOLE = `http://127.0.0.1:${hostPort + 1}`;
  const FOREIGN = `http://127.0.0.1:${hostPort + 2}`;
  const listener = await import(pathToFileURL(LISTENER).href);
  const host = await createHost({
    name: "xo",
    port: hostPort,
    bind: "127.0.0.1",
    fixtures,
    baseConfig: { VITE_OMEGA_CONSOLE_URL: `${CONSOLE}/omega/console/?embed=1` },
  });
  const consoleConfig = {
    origin: CONSOLE,
    frameAncestors: [HOST],
    staticDir: originDir,
    trustedTypes: process.env.CONSOLE_E2E_TRUSTED_TYPES || "report",
    host: "127.0.0.1",
    port: hostPort + 1,
  };
  const origin = { csp: [], api: [], used: new Set(), fault: null, served: [] };
  const resetOrigin = () => {
    origin.csp = [];
    origin.api = [];
    origin.used = new Set();
    origin.fault = null;
    origin.served = [];
  };

  async function api(req, res, url) {
    const id = requestId();
    const headers = {
      ...listener.securityHeaders("local"),
      "x-omega-request-id": id,
    };
    const apiPath = url.pathname.slice("/omega/api".length) + url.search;
    const bearer = /^Bearer (.+)$/.exec(req.headers.authorization || "")?.[1];
    const keyHash = bearer ? sha256(bearer) : null;
    const replayed = Boolean(keyHash) && origin.used.has(keyHash);
    const known = Boolean(bearer) && host.state.minted.has(bearer);
    origin.api.push({
      method: req.method,
      path: apiPath,
      keyHash,
      replayed,
      known,
      id,
    });
    if (!apiPath.startsWith("/workspace")) {
      return json(res, 403, { error: "route_not_allowed" }, headers);
    }
    if (!known || replayed) {
      return json(
        res,
        401,
        {
          schemaVersion: "omega-workspace/v1",
          error: {
            code: replayed ? "assertion_replayed" : "unauthenticated",
            message: "Sign in again.",
          },
        },
        headers,
      );
    }
    origin.used.add(keyHash);
    const body =
      req.method === "POST" || req.method === "PATCH"
        ? JSON.parse((await readText(req)) || "null") ?? undefined
        : undefined;
    const result = fixtures.handleWorkspace(
      host.state.tenant,
      req.method,
      apiPath,
      body,
    );
    const fault = origin.fault;
    if (fault && fault.path === url.pathname.slice("/omega/api".length)) {
      origin.served.push(id);
      if (fault.times !== undefined && (fault.times -= 1) <= 0)
        origin.fault = null;
      // A JSON body cut off mid-way (the fault proxy of CP P8).
      return json(res, 200, JSON.stringify(result.body).slice(0, 12), headers);
    }
    return json(res, result.status, result.body, headers);
  }

  async function control(req, res, url) {
    if (url.pathname === "/__e2e/origin-state") {
      return json(res, 200, {
        csp: origin.csp,
        api: origin.api,
        served: origin.served,
        consoleConfig,
      });
    }
    if (url.pathname === "/__e2e/origin-reset" && req.method === "POST") {
      resetOrigin();
      return json(res, 200, { ok: true });
    }
    if (url.pathname === "/__e2e/origin-fault" && req.method === "POST") {
      origin.fault = JSON.parse((await readText(req)) || "null");
      return json(res, 200, { ok: true });
    }
    return json(res, 404, { error: "not_found" });
  }

  const consoleServer = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", CONSOLE);
      if (url.pathname.startsWith("/__e2e/"))
        return await control(req, res, url);
      if (url.pathname === listener.CSP_REPORT_PATH && req.method === "POST") {
        const report = await listener.readCspReport(req);
        if (report) origin.csp.push(report);
        res.writeHead(204);
        return res.end();
      }
      if (url.pathname.startsWith("/omega/api/"))
        return await api(req, res, url);
      if (
        listener.serveStatic(req, res, url.pathname, consoleConfig, {
          mode: "local",
        })
      )
        return undefined;
      return json(
        res,
        404,
        { error: "not_found" },
        listener.securityHeaders("local"),
      );
    } catch (error) {
      console.error("[console-host] console origin:", error);
      return json(res, 500, { error: "harness_error" });
    }
  });

  const pages = foreignPages(CONSOLE);
  const foreignServer = http.createServer((req, res) => {
    const url = new URL(req.url || "/", FOREIGN);
    const page = pages[url.pathname];
    res.writeHead(page ? 200 : 404, {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    });
    res.end(page || "Not found");
  });

  await listen(consoleServer, hostPort + 1);
  await listen(foreignServer, hostPort + 2);
  console.log(
    `[console-host] console origin on ${CONSOLE} (F-GATE listener ${path.relative(process.cwd(), LISTENER)})`,
  );
  console.log(`[console-host] foreign site on ${FOREIGN}`);
  return {
    host,
    servers: [consoleServer, foreignServer],
    origins: { HOST, CONSOLE, FOREIGN },
  };
}

module.exports = { startCrossOrigin };

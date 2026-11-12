#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Builds the real Future AGI frontend with only the router module swapped for
// harness/HarnessRoutes.jsx (HUB_ROUTES under /dashboard), then serves it on
// one loopback origin that also proxies every API path to the Hub backend, so
// the app's own axios talks to it same-origin (no CORS, no cloud):
//
//   HUB_HARNESS_PORT     default 3041
//   HUB_E2E_BACKEND      default http://127.0.0.1:8741 (scripts/omega-hub/dev-up.sh)
//   HUB_E2E_WORK         scratch dir for the build
//   HUB_HARNESS_REUSE=1  serve an existing build without rebuilding
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const ROUTER_INDEX = path.join(FRONTEND, "src/routes/sections/index.jsx");
const HARNESS_ROUTES = path.join(__dirname, "harness/HarnessRoutes.jsx");
const PORT = Number(process.env.HUB_HARNESS_PORT || 3041);
const BACKEND = new URL(process.env.HUB_E2E_BACKEND || "http://127.0.0.1:8741");
const WORK = process.env.HUB_E2E_WORK || path.join(os.tmpdir(), "hubfe-e2e");
const OUT = path.join(WORK, "dist");
const ORIGIN = `http://127.0.0.1:${PORT}`;

const HARNESS_ENV = {
  VITE_ENVIRONMENT: "dev",
  VITE_HOST_API: ORIGIN,
  VITE_ENABLE_MSW: "false",
  VITE_REACT_QUERY_DEVTOOLS_ENABLED: "false",
  VITE_API_CONTRACTS: "off",
};

function harnessPlugin() {
  return {
    name: "omega-hub-harness",
    enforce: "pre",
    load(id) {
      if (id === ROUTER_INDEX) return `export { default } from ${JSON.stringify(HARNESS_ROUTES)};`;
      return null;
    },
  };
}

async function build() {
  const vite = await import("vite");
  const react = (await import("@vitejs/plugin-react-swc")).default;
  Object.assign(process.env, HARNESS_ENV);
  const envDir = path.join(WORK, "env");
  fs.mkdirSync(envDir, { recursive: true });
  await vite.build({
    root: FRONTEND,
    configFile: false,
    envDir,
    mode: "production",
    cacheDir: path.join(WORK, "vite-cache"),
    logLevel: "warn",
    plugins: [react(), harnessPlugin()],
    resolve: {
      alias: [
        { find: /^~(.+)/, replacement: path.join(FRONTEND, "node_modules/$1") },
        { find: /^src(.+)/, replacement: path.join(FRONTEND, "src/$1") },
      ],
    },
    build: {
      outDir: OUT,
      emptyOutDir: true,
      sourcemap: false,
      modulePreload: false,
      reportCompressedSize: false,
      chunkSizeWarningLimit: 100000,
    },
  });
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json", ".ico": "image/x-icon", ".webp": "image/webp", ".jpg": "image/jpeg" };
const isPage = (p) => p === "/" || p.startsWith("/dashboard") || p.startsWith("/auth");

function proxy(req, res) {
  const target = http.request(
    { hostname: BACKEND.hostname, port: BACKEND.port, path: req.url, method: req.method, headers: { ...req.headers, host: BACKEND.host } },
    (upstream) => {
      res.writeHead(upstream.statusCode, upstream.headers);
      upstream.pipe(res);
    },
  );
  target.on("error", (error) => {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: { code: "HARNESS_BACKEND_DOWN", message: error.message } }));
  });
  req.pipe(target);
}

function serve() {
  const server = http.createServer((req, res) => {
    const { pathname } = new URL(req.url, ORIGIN);
    const file = path.join(OUT, decodeURIComponent(pathname));
    if (file.startsWith(OUT) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
      fs.createReadStream(file).pipe(res);
    } else if (isPage(pathname)) {
      res.writeHead(200, { "content-type": "text/html" });
      fs.createReadStream(path.join(OUT, "index.html")).pipe(res);
    } else {
      proxy(req, res);
    }
  });
  server.listen(PORT, "127.0.0.1", () => console.log(`hub harness: ${ORIGIN} -> ${BACKEND.origin}`));
  const stop = () => server.close(() => process.exit(0));
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}

(async () => {
  if (process.env.HUB_HARNESS_REUSE !== "1" || !fs.existsSync(path.join(OUT, "index.html"))) {
    console.log(`building the harness into ${OUT}`);
    await build();
  }
  serve();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

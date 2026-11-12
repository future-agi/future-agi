/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Builds the real Future AGI frontend once for the console-frame e2e, with
// only the router module swapped for a test router (harness/HarnessRoutes.jsx)
// that mounts the real ConsoleFramePage at /dashboard/console/*. Sentry is on
// and points at the harness's own sink (sentry-sink.cjs, CP P8).
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { sentryDsn } = require("./sentry-sink.cjs");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const ROUTER_INDEX = path.join(FRONTEND, "src/routes/sections/index.jsx");
const HARNESS_ROUTES = path.join(__dirname, "harness/HarnessRoutes.jsx");
const WORK =
  process.env.CONSOLE_E2E_WORK ||
  path.join(os.tmpdir(), "omega-console-frame-e2e");
const DIST = path.join(WORK, "dist");
const FAKE_PORT = Number(process.env.CONSOLE_E2E_FAKE_PORT || 3061);

function harnessPlugin() {
  return {
    name: "omega-console-frame-harness",
    enforce: "pre",
    load(id) {
      if (id === ROUTER_INDEX) {
        return `export { default } from ${JSON.stringify(HARNESS_ROUTES)};`;
      }
      return null;
    },
  };
}

async function build() {
  if (
    process.env.CONSOLE_E2E_REUSE_BUILD === "1" &&
    fs.existsSync(path.join(DIST, "index.html"))
  ) {
    console.log("[console-host] reusing the existing build");
    return;
  }
  const vite = await import("vite");
  const react = (await import("@vitejs/plugin-react-swc")).default;
  Object.assign(process.env, {
    VITE_ENVIRONMENT: "dev",
    VITE_ENABLE_MSW: "false",
    VITE_REACT_QUERY_DEVTOOLS_ENABLED: "false",
    // Sentry on, pointed at this harness's sink (sentry-sink.cjs, CP P8).
    VITE_SENTRY_DSN: sentryDsn(FAKE_PORT),
    VITE_SENTRY_TRACES_SAMPLE_RATE: "1",
  });
  const envDir = path.join(WORK, "env");
  fs.mkdirSync(envDir, { recursive: true });
  const started = Date.now();
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
      outDir: DIST,
      emptyOutDir: true,
      sourcemap: false,
      modulePreload: false,
      reportCompressedSize: false,
      chunkSizeWarningLimit: 100000,
      target: "esnext",
    },
  });
  console.log(
    `[console-host] built in ${Math.round((Date.now() - started) / 1000)}s`,
  );
}

module.exports = { DIST, build };

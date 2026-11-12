#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Builds the real Future AGI frontend and serves it on
// http://localhost:3051 (STUDIO_E2E_PARENT_PORT). Only the router module is
// swapped for harness/HarnessRoutes.jsx, which registers the Studio tab the
// way the shell does; index.html (with its connect intake), main.jsx,
// app.jsx and every analytics SDK are the real ones, configured with keys so
// each SDK starts. Playwright answers every analytics host locally, so
// nothing leaves this computer.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const ROUTER_INDEX = path.join(FRONTEND, "src/routes/sections/index.jsx");
const HARNESS_ROUTES = path.join(__dirname, "harness/HarnessRoutes.jsx");
const WORK =
  process.env.STUDIO_E2E_WORK || path.join(os.tmpdir(), "omega-studio-tab-e2e");
const PORT = Number(process.env.STUDIO_E2E_PARENT_PORT || 3051);

// Analytics configured the way a hosted build is, pointed at nothing real.
const HARNESS_ENV = {
  VITE_ENVIRONMENT: "dev",
  VITE_HOST_API: "http://127.0.0.1:4199",
  VITE_ENABLE_MSW: "false",
  VITE_REACT_QUERY_DEVTOOLS_ENABLED: "false",
  VITE_GTM_ID: "GTM-OMEGATEST",
  VITE_POSTHOG_KEY: "phc_omega_studio_test",
  VITE_MIXPANEL_TOKEN: "mp_omega_studio_test",
  VITE_MIXPANEL_SESSION_REPLAY_PERCENT: "100",
  VITE_SENTRY_DSN:
    "https://0123456789abcdef0123456789abcdef@o1.ingest.sentry.io/1",
  VITE_SENTRY_TRACES_SAMPLE_RATE: "1",
  VITE_NEWRELIC_LICENSE_KEY: "NRJS-0000000000000000000",
  VITE_NEWRELIC_ACCOUNT_ID: "1",
  VITE_NEWRELIC_DEV_APP_ID: "1",
};

const harnessPlugin = {
  name: "omega-studio-harness",
  enforce: "pre",
  load(id) {
    if (id === ROUTER_INDEX) {
      return `export { default } from ${JSON.stringify(HARNESS_ROUTES)};`;
    }
    return null;
  },
};

async function main() {
  const vite = await import("vite");
  const react = (await import("@vitejs/plugin-react-swc")).default;
  Object.assign(process.env, HARNESS_ENV);
  const envDir = path.join(WORK, "env");
  fs.mkdirSync(envDir, { recursive: true });
  const outDir = path.join(WORK, "dist");
  const config = {
    root: FRONTEND,
    configFile: false,
    envDir,
    cacheDir: path.join(WORK, "vite-cache"),
    logLevel: "warn",
    plugins: [react(), harnessPlugin],
    resolve: {
      alias: [
        { find: /^~(.+)/, replacement: path.join(FRONTEND, "node_modules/$1") },
        { find: /^src(.+)/, replacement: path.join(FRONTEND, "src/$1") },
      ],
    },
    build: {
      outDir,
      emptyOutDir: true,
      sourcemap: false,
      modulePreload: false,
      reportCompressedSize: false,
      chunkSizeWarningLimit: 100000,
      target: "esnext",
    },
    preview: { port: PORT, strictPort: true, host: "localhost" },
  };
  if (process.env.STUDIO_E2E_REUSE_BUILD !== "1" || !fs.existsSync(outDir)) {
    const started = Date.now();
    await vite.build({ ...config, mode: "production" });
    console.log(
      `[harness] built in ${Math.round((Date.now() - started) / 1000)}s`,
    );
  }
  const server = await vite.preview(config);
  console.log(`[harness] Future AGI on http://localhost:${PORT}`);

  const stop = async () => {
    await server.close?.();
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

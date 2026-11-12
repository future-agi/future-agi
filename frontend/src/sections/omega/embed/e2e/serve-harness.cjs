#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Builds the real Future AGI frontend twice and serves both builds:
//   guarded  http://localhost:3031  the code as shipped
//   control  http://localhost:3033  the same build with the guard switched off
//            (the index.html intake script removed and the SDK hooks made
//            pass-through), so the sink test can prove it would see a leak.
// Only the router module is swapped for a test router that renders
// EmbeddedSurface; index.html, main.jsx, app.jsx and every analytics SDK are
// the real ones. All analytics hosts are fulfilled by Playwright, so nothing
// leaves this computer.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { PORTS } = require("./ports.cjs");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const SRC = path.join(FRONTEND, "src");
const ROUTER_INDEX = path.join(SRC, "routes/sections/index.jsx");
const HARNESS_ROUTES = path.join(__dirname, "harness/HarnessRoutes.jsx");
const SDK_GUARDS = path.join(
  SRC,
  "sections/omega/embed/telemetry/sdk-guards.js",
);
const SDK_GUARDS_OFF = path.join(__dirname, "harness/sdk-guards.off.js");
const WORK =
  process.env.EMBED_E2E_WORK || path.join(os.tmpdir(), "omega-embed-host-e2e");
const INTAKE = /<script data-omega-connect-intake>[\s\S]*?<\/script>/;

// Analytics configured the way a hosted build is, pointed at nothing real.
const HARNESS_ENV = {
  VITE_ENVIRONMENT: "dev",
  VITE_HOST_API: "http://127.0.0.1:4199",
  VITE_ENABLE_MSW: "false",
  VITE_REACT_QUERY_DEVTOOLS_ENABLED: "false",
  VITE_GTM_ID: "GTM-OMEGATEST",
  VITE_POSTHOG_KEY: "phc_omega_embed_test",
  VITE_MIXPANEL_TOKEN: "mp_omega_embed_test",
  VITE_MIXPANEL_SESSION_REPLAY_PERCENT: "100",
  VITE_SENTRY_DSN:
    "https://0123456789abcdef0123456789abcdef@o1.ingest.sentry.io/1",
  VITE_SENTRY_TRACES_SAMPLE_RATE: "1",
  VITE_NEWRELIC_LICENSE_KEY: "NRJS-0000000000000000000",
  VITE_NEWRELIC_ACCOUNT_ID: "1",
  VITE_NEWRELIC_DEV_APP_ID: "1",
};

const BUILDS = [
  {
    name: "guarded",
    port: PORTS.parent,
    guard: true,
  },
  {
    name: "control",
    port: PORTS.control,
    guard: false,
  },
];

function harnessPlugin({ guard }) {
  return {
    name: "omega-embed-harness",
    enforce: "pre",
    load(id) {
      if (id === ROUTER_INDEX) {
        return `export { default } from ${JSON.stringify(HARNESS_ROUTES)};`;
      }
      if (!guard && id === SDK_GUARDS) {
        return `export * from ${JSON.stringify(SDK_GUARDS_OFF)};`;
      }
      return null;
    },
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        if (guard) return html;
        if (!INTAKE.test(html))
          throw new Error("intake script not found in index.html");
        return html.replace(INTAKE, "");
      },
    },
  };
}

async function main() {
  const vite = await import("vite");
  const react = (await import("@vitejs/plugin-react-swc")).default;
  Object.assign(process.env, HARNESS_ENV);
  const envDir = path.join(WORK, "env");
  fs.mkdirSync(envDir, { recursive: true });

  const servers = [];
  for (const build of BUILDS) {
    const outDir = path.join(WORK, `dist-${build.name}`);
    const config = {
      root: FRONTEND,
      configFile: false,
      envDir,
      cacheDir: path.join(WORK, `vite-cache-${build.name}`),
      logLevel: "warn",
      plugins: [react(), harnessPlugin(build)],
      resolve: {
        alias: [
          {
            find: /^~(.+)/,
            replacement: path.join(FRONTEND, "node_modules/$1"),
          },
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
      preview: { port: build.port, strictPort: true, host: "localhost" },
    };
    if (process.env.EMBED_E2E_REUSE_BUILD !== "1" || !fs.existsSync(outDir)) {
      const started = Date.now();
      await vite.build({ ...config, mode: "production" });
      console.log(
        `[harness] built ${build.name} in ${Math.round((Date.now() - started) / 1000)}s`,
      );
    }
    servers.push(await vite.preview(config));
    console.log(`[harness] ${build.name} on http://localhost:${build.port}`);
  }

  const stop = async () => {
    await Promise.all(servers.map((server) => server.close?.()));
    process.exit(0);
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

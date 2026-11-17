/* eslint-env node */
// Playwright config for the Studio tab e2e. Run with ./run-e2e.sh, which
// points NODE_PATH at a Playwright install and isolates HOME.
const os = require("node:os");
const path = require("node:path");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const WORK =
  process.env.STUDIO_E2E_WORK || path.join(os.tmpdir(), "omega-studio-tab-e2e");
const PORT = Number(process.env.STUDIO_E2E_PARENT_PORT || 3051);

module.exports = {
  testDir: __dirname,
  testMatch: /.*\.e2e\.cjs$/,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.join(WORK, "results"),
  reporter: [["list"]],
  use: {
    serviceWorkers: "block",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "firefox", use: { browserName: "firefox" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
  webServer: [
    {
      command: `node ${JSON.stringify(path.join(__dirname, "serve-harness.cjs"))}`,
      cwd: FRONTEND,
      url: `http://localhost:${PORT}/`,
      reuseExistingServer: true,
      timeout: 1_200_000,
      env: {
        STUDIO_E2E_WORK: WORK,
        STUDIO_E2E_PARENT_PORT: String(PORT),
        NODE_OPTIONS: "--max-old-space-size=6144",
      },
    },
  ],
};

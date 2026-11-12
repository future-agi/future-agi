/* eslint-env node */
// Playwright config for the Hub e2e against a real local backend. Run with
// ./run-e2e.sh, which points NODE_PATH at a Playwright install and isolates HOME.
const os = require("node:os");
const path = require("node:path");

const PORT = Number(process.env.HUB_HARNESS_PORT || 3041);
const WORK = process.env.HUB_E2E_WORK || path.join(os.tmpdir(), "hubfe-e2e");

module.exports = {
  testDir: __dirname,
  testMatch: /.*\.e2e\.cjs$/,
  timeout: 15 * 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.join(WORK, "results"),
  reporter: [["list"]],
  use: {
    browserName: "chromium",
    serviceWorkers: "block",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 900 },
  },
  // Never reuse a server already on the port: it could be another build.
  webServer: [
    {
      command: `node ${JSON.stringify(path.join(__dirname, "serve-harness.cjs"))}`,
      url: `http://127.0.0.1:${PORT}/dashboard/hub`,
      reuseExistingServer: false,
      timeout: 900_000,
      env: { HUB_E2E_WORK: WORK, NODE_OPTIONS: "--max-old-space-size=8192" },
    },
  ],
};

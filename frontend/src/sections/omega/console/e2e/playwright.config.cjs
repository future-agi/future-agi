/* eslint-env node */
// Playwright config for the console-frame e2e. Run with ./run-e2e.sh, which
// points NODE_PATH at a Playwright install and isolates HOME.
const os = require("node:os");
const path = require("node:path");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const WORK =
  process.env.CONSOLE_E2E_WORK ||
  path.join(os.tmpdir(), "omega-console-frame-e2e");
const FAKE_PORT = Number(process.env.CONSOLE_E2E_FAKE_PORT || 3061);
const NOTIFY = /notify\.e2e\.cjs$/;
// The strict real-child lifecycle gate is opt-in via the full-stack config.
// Its stand-in cases remain in all three default browser projects.
const REAL_EE_LIFECYCLE = /Console document lifecycle with real EE Console/;

module.exports = {
  testDir: __dirname,
  testMatch: /.*\.e2e\.cjs$/,
  timeout: 120_000,
  expect: { timeout: 20_000 },
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
    {
      name: "chromium",
      use: { browserName: "chromium" },
      testIgnore: NOTIFY,
      grepInvert: REAL_EE_LIFECYCLE,
    },
    {
      name: "firefox",
      use: { browserName: "firefox" },
      testIgnore: NOTIFY,
      grepInvert: REAL_EE_LIFECYCLE,
    },
    {
      name: "webkit",
      use: { browserName: "webkit" },
      testIgnore: NOTIFY,
      grepInvert: REAL_EE_LIFECYCLE,
    },
    // Notifications need the full Chromium build (new headless): the headless
    // shell has none, and Playwright grants the permission in Chromium only.
    {
      name: "chromium-notifications",
      use: { browserName: "chromium", channel: "chromium" },
      testMatch: NOTIFY,
    },
  ],
  webServer: [
    {
      command: `node ${JSON.stringify(path.join(__dirname, "console-host.cjs"))}`,
      cwd: FRONTEND,
      url: `http://localhost:${FAKE_PORT}/__e2e/state`,
      reuseExistingServer: false,
      timeout: 1_200_000,
      stdout: "pipe",
      env: {
        CONSOLE_E2E_WORK: WORK,
        NODE_OPTIONS: "--max-old-space-size=6144",
      },
    },
  ],
};

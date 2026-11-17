/* eslint-env node */
// Playwright config for the embed-host e2e. Run with ./run-e2e.sh, which
// points NODE_PATH at a Playwright install and isolates HOME.
const os = require("node:os");
const path = require("node:path");

const { CHILD, CONTROL } = require("./ports.cjs");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const WORK =
  process.env.EMBED_E2E_WORK || path.join(os.tmpdir(), "omega-embed-host-e2e");

module.exports = {
  testDir: __dirname,
  testMatch: /.*\.e2e\.cjs$/,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: Number(process.env.EMBED_E2E_WORKERS || 1),
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
  // Never reuse a server already on these ports: a local Studio left running
  // on the child's port would stand in for the fake child. Move a busy port
  // with EMBED_*_PORT (see ports.cjs).
  webServer: [
    {
      command: `node ${JSON.stringify(path.join(__dirname, "child-server.cjs"))}`,
      url: `${CHILD}/probe.json`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `node ${JSON.stringify(path.join(__dirname, "serve-harness.cjs"))}`,
      cwd: FRONTEND,
      url: `${CONTROL}/`,
      reuseExistingServer: false,
      timeout: 900_000,
      env: { EMBED_E2E_WORK: WORK, NODE_OPTIONS: "--max-old-space-size=6144" },
    },
  ],
};

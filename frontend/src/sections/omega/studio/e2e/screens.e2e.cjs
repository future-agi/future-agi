/* eslint-env node */
// Screenshots of every Studio tab state at 1280 and 390 wide, light and
// dark, for the owner's review. Chromium only. Every state is reached the
// way a person reaches it, against the real omega dev: nothing is seeded.
// Set STUDIO_SCREENS_DIR.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect } = require("playwright/test");

const { attachSink } = require("../../embed/e2e/sink.cjs");
const { deadPort, killAllOmegaDev, startOmegaDev } = require("./omega-dev.cjs");
const { runRouterInFrame, studioFrame } = require("./studio-checks.cjs");

const PORT = Number(process.env.STUDIO_E2E_PARENT_PORT || 3051);
const PARENT = `http://localhost:${PORT}`;
const DIR =
  process.env.STUDIO_SCREENS_DIR ||
  path.join(process.env.STUDIO_E2E_WORK || os.tmpdir(), "screens");
const SIZES = [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
];
// Longer than Studio's 3 s wait for a key plus the tab's 1.5 s grace, and
// than EmbeddedSurface's 8 s bound for a child that never reports a gate.
const KEPT_KEY_WAIT = 20_000;

const status = (page) => page.getByTestId("studio-status");
const gate = (page) => page.getByTestId("omega-embed-gate");

let embedded;
let plain;

test.describe.configure({ mode: "serial" });
test.skip(
  ({ browserName }) => browserName !== "chromium",
  "screenshots use Chromium",
);

test.beforeAll(async ({ browserName }) => {
  if (browserName !== "chromium") return;
  fs.mkdirSync(DIR, { recursive: true });
  embedded = await startOmegaDev({
    name: "screens-embed",
    embedOrigins: [PARENT],
  });
  plain = await startOmegaDev({ name: "screens-plain" });
});

test.afterAll(async () => {
  await embedded?.stop();
  await plain?.stop();
  killAllOmegaDev();
});

async function settle(page) {
  await page.evaluate(() => document.fonts?.ready);
  await page.waitForTimeout(500);
}

// The app scrolls inside its own main area (as DashboardLayout's Main does),
// so a "full page" shot grows the viewport to the page's height instead.
async function fitViewportToPage(page, size) {
  const height = await page.evaluate(
    () =>
      document.querySelector('[data-testid="omega-tab-studio"]')?.scrollHeight,
  );
  if (height && height > size.height) {
    await page.setViewportSize({ width: size.width, height: height + 32 });
    await page.waitForTimeout(200);
  }
}

async function frameTheme(page, mode) {
  const frame = await studioFrame(page, embedded.url);
  await frame.waitForFunction(
    (wanted) => document.documentElement.dataset.theme === wanted,
    mode,
    { timeout: 15_000 },
  );
  return frame;
}

const STATES = {
  async "connect-empty"(page) {
    await page.goto(`${PARENT}/dashboard/studio`);
    await expect(page.getByTestId("studio-connect")).toBeVisible();
    await expect(page.getByTestId("studio-saved")).toHaveCount(0);
    return { fullPage: true };
  },

  async "connect-saved"(page) {
    await page.goto(embedded.futureAgiLink);
    await expect(status(page)).toHaveAttribute("data-state", "connected", {
      timeout: 60_000,
    });
    await page.getByTestId("studio-disconnect").click();
    await page.getByRole("button", { name: /^Rename / }).click();
    await page.getByTestId("studio-saved-label-input").fill("studio-fixture");
    await page.getByTestId("studio-saved-label-input").press("Enter");
    await expect(page.getByTestId("studio-saved-label")).toHaveText(
      "studio-fixture",
    );
    await page.mouse.move(0, 0);
    return { fullPage: true };
  },

  // The run is started at desktop width (Studio's narrow layout has no run
  // form), then the window is set to the size being captured.
  async connected(page, mode, size) {
    await page.setViewportSize(SIZES[0]);
    await page.goto(embedded.futureAgiLink);
    await expect(status(page)).toHaveAttribute("data-state", "connected", {
      timeout: 60_000,
    });
    const frame = await frameTheme(page, mode);
    const runId = await runRouterInFrame(frame);
    expect(runId).not.toBe("");
    const { runs } = await embedded.api("/studio/v1/runs");
    expect(runs.some((run) => run.id === runId)).toBe(true);
    await page.setViewportSize(size);
    await frameTheme(page, mode);
    return { fullPage: false };
  },

  async "framing-blocked"(page) {
    await page.goto(
      `${PARENT}/dashboard/studio#connect=${plain.url}&t=${plain.token}`,
    );
    await expect(gate(page)).toHaveAttribute("data-cause", "framing-blocked", {
      timeout: 20_000,
    });
    return { fullPage: false };
  },

  async "not-running"(page) {
    const port = await deadPort();
    await page.goto(
      `${PARENT}/dashboard/studio#connect=http://127.0.0.1:${port}&t=x`,
    );
    await expect(gate(page)).toHaveAttribute("data-cause", "not-running", {
      timeout: 20_000,
    });
    return { fullPage: false };
  },

  // A person pastes the bare address, without the key: Studio answers that
  // it has none for this tab, so the tab asks for the link again.
  async "needs-new-link"(page) {
    await page.goto(`${PARENT}/dashboard/studio`);
    await page
      .getByTestId("studio-connect-input")
      .fill(embedded.url.replace("http://", ""));
    await page.getByTestId("studio-connect-submit").click();
    await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
      "data-kind",
      "needs-key",
      { timeout: KEPT_KEY_WAIT },
    );
    return { fullPage: true };
  },
};

for (const mode of ["light", "dark"]) {
  for (const size of SIZES) {
    for (const [name, reach] of Object.entries(STATES)) {
      test(`${name} ${size.width} ${mode}`, async ({ page, context }) => {
        await attachSink(context, { mainPage: () => page });
        await page.setViewportSize(size);
        await page.emulateMedia({ colorScheme: mode });
        const { fullPage } = await reach(page, mode, size);
        await settle(page);
        if (fullPage) await fitViewportToPage(page, size);
        await page.screenshot({
          path: path.join(DIR, `studio-${name}-${size.width}-${mode}.png`),
        });
      });
    }
  }
}

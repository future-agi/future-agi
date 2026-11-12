/* eslint-env node */
// Screenshots of the Console frame page at 1280 and 390 wide, light and dark:
// connected (fake Console, and the EE Console when its build is available),
// connecting, a role without Omega, and the Console not answering.
// Chromium only; written to CONSOLE_SCREENS_DIR.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { expect, test } = require("playwright/test");

const {
  EE,
  FAKE,
  HAS_EE,
  control,
  expectConnected,
  keepLocal,
  waitForConsoleFrame,
} = require("./helpers.cjs");

const DIR =
  process.env.CONSOLE_SCREENS_DIR ||
  path.join(os.tmpdir(), "omega-console-frame-e2e", "screens");
const WIDTHS = [1280, 390];
const THEMES = ["light", "dark"];

// The host's route progress bar (#nprogress) fades out after a page load; the
// picture waits for it so it shows the page at rest.
async function shot(page, name, width, theme) {
  await expect(page.locator("#nprogress")).toHaveCount(0);
  await page.screenshot({
    path: path.join(DIR, `${name}-${width}-${theme}.png`),
  });
}

test.describe("Console frame screenshots", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "Chromium only");

  test.beforeAll(() => fs.mkdirSync(DIR, { recursive: true }));

  for (const width of WIDTHS) {
    for (const theme of THEMES) {
      test(`states at ${width}px, ${theme}`, async ({ browser, request }) => {
        const context = await browser.newContext({
          viewport: { width, height: 800 },
          colorScheme: theme,
        });
        await keepLocal(context);
        await context.addInitScript((mode) => {
          window.localStorage.setItem(
            "settings",
            JSON.stringify({ themeMode: mode }),
          );
        }, theme);
        const page = await context.newPage();
        const fake = control(request, FAKE);

        await fake.reset();
        await page.goto(`${FAKE}/dashboard/console/agents`);
        await expectConnected(page);
        const frame = await waitForConsoleFrame(page);
        await expect(frame.locator("#theme")).toHaveText(theme);
        await shot(page, "connected-fake", width, theme);
        expect(
          await page.evaluate(() => document.scrollingElement.scrollWidth),
        ).toBeLessThanOrEqual(width);

        await fake.mode({ exchange: "hold" });
        await page.goto(`${FAKE}/dashboard/console`);
        await expect(page.getByTestId("omega-embed-gate")).toHaveAttribute(
          "data-state",
          "connecting",
        );
        // Let the spinner draw its arc.
        await page.waitForTimeout(500);
        await shot(page, "connecting", width, theme);

        await fake.reset();
        await fake.mode({ exchange: "refused" });
        await page.goto(`${FAKE}/dashboard/console`);
        await expect(page.getByTestId("omega-embed-gate")).toHaveAttribute(
          "data-state",
          "needs-key",
        );
        await shot(page, "role-refused", width, theme);

        await fake.reset();
        await fake.mode({ consoleUp: false });
        await page.goto(`${FAKE}/dashboard/console`);
        await expect(page.getByTestId("omega-embed-gate")).toHaveAttribute(
          "data-state",
          "timeout",
          { timeout: 30_000 },
        );
        await shot(page, "unavailable", width, theme);
        await fake.reset();

        if (HAS_EE) {
          const ee = control(request, EE);
          await ee.reset();
          for (const route of ["chat", "agents"]) {
            await page.goto(`${EE}/dashboard/console/${route}`);
            await expectConnected(page);
            const eeFrame = await waitForConsoleFrame(page);
            // At 390 px the Console keeps its sidebar in a drawer behind
            // "Open navigation" (ee/p-console-shell); either one means it is up.
            await expect(
              eeFrame
                .getByRole("navigation", { name: "Omega Console" })
                .or(eeFrame.getByRole("button", { name: "Open navigation" }))
                .first(),
            ).toBeAttached();
            // Let the Console's first data load settle before the picture.
            await page.waitForTimeout(1_500);
            await shot(page, `connected-ee-${route}`, width, theme);
          }
        }
        await context.close();
      });
    }
  }
});

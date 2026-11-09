/* eslint-env node */
// CP P15's opt-in notifications in real Chromium (full build, new headless:
// the headless shell has no notifications). The permission comes from
// Playwright's grant and the hidden tab from an override in the page, so this
// proves the host's wiring and the browser's Notification API, not the OS.
const { expect, test } = require("playwright/test");

const {
  FAKE,
  control,
  expectConnected,
  keepLocal,
  waitForConsoleFrame,
} = require("./helpers.cjs");
const { noteBrowser, writeReceipt } = require("./evidence.cjs");

// Runs in the config's chromium-notifications project only (full Chromium).
test.describe("Console host: Inbox notifications (CP P15)", () => {
  let host;

  test.beforeEach(async ({ browser, context, request }) => {
    noteBrowser(browser);
    await keepLocal(context);
    host = control(request, FAKE);
    await host.reset();
  });

  test("notifies once when the count rises while the tab is hidden, only after the opt-in", async ({
    page,
    context,
  }, testInfo) => {
    await context.grantPermissions(["notifications"], { origin: FAKE });
    await page.addInitScript(() => {
      if (window.top !== window) return;
      window.__shown = [];
      const Real = window.Notification;
      class Recording extends Real {
        constructor(title, options) {
          super(title, options);
          window.__shown.push({
            title,
            body: options?.body,
            tag: options?.tag,
          });
        }
      }
      window.Notification = Recording;
      window.__hide = () =>
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          get: () => "hidden",
        });
    });
    await page.goto(`${FAKE}/dashboard`);
    await page.getByTestId("omega-product-switcher").click();
    const optIn = page.getByTestId("omega-console-notify");
    await expect(optIn).toHaveAttribute("data-state", "off");
    await optIn.click();
    await page.getByTestId("omega-product-switcher").click();
    await expect(page.getByTestId("omega-console-notify")).toHaveAttribute(
      "data-state",
      "on",
    );
    await page.keyboard.press("Escape");

    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    const badge = (inbox) =>
      frame.evaluate(
        (n) =>
          window.parent.postMessage(
            { type: "embed.badge", inbox: n },
            location.origin,
          ),
        inbox,
      );
    await badge(1); // visible: no notification
    await expect(page).toHaveTitle("(1) Omega Console");
    await page.evaluate(() => window.__hide());
    await badge(1); // same count: none
    await badge(4); // rose while hidden: one
    await expect.poll(() => page.evaluate(() => window.__shown.length)).toBe(1);
    await badge(2); // fell: none
    await page.waitForTimeout(500);
    const shown = await page.evaluate(() => window.__shown);
    expect(shown).toEqual([
      {
        title: "Omega Console",
        body: "4 approvals waiting in Omega Console",
        tag: "omega-console-inbox",
      },
    ]);
    writeReceipt(testInfo, "P15 notifications", {
      permission: "granted (Playwright grant)",
      visibility: "hidden (overridden in the page)",
      shown,
    });
  });
});

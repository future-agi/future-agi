/* eslint-env node */
// CP A11Y1 for the parts Future AGI draws around the Console (lane F-HOST):
// axe-core on the host document only (the framed Console is the EE lanes'
// own axe run), in each host state, light and dark: the frame connected, the
// viewer's panel, the sign-in refusal panel, and the product switcher open
// with the Inbox count. Bar: 0 serious and 0 critical. AXE_CORE_FILE names
// axe.min.js (axe-core 4.13.0, read from an existing install).
const fs = require("node:fs");
const { expect, test } = require("playwright/test");

const {
  FAKE,
  control,
  expectConnected,
  keepLocal,
  setHostTheme,
  waitForConsoleFrame,
} = require("./helpers.cjs");
const { noteBrowser, writeReceipt } = require("./evidence.cjs");

const AXE = process.env.AXE_CORE_FILE || "";

async function runAxe(page) {
  await page.addScriptTag({ content: fs.readFileSync(AXE, "utf8") });
  return page.evaluate(async () => {
    // The frame's own document is not Future AGI's; only the frame element is.
    const result = await window.axe.run(document, {
      iframes: false,
      resultTypes: ["violations"],
    });
    return result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.length,
      targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
      html: v.nodes.slice(0, 3).map((n) => n.html.slice(0, 160)),
      summary: v.nodes
        .slice(0, 1)
        .map((n) => (n.failureSummary || "").slice(0, 300)),
    }));
  });
}

const STATES = [
  {
    name: "frame connected",
    async open(page) {
      await page.goto(`${FAKE}/dashboard/console/agents`);
      await expectConnected(page);
      await waitForConsoleFrame(page);
    },
  },
  {
    name: "viewer panel",
    mode: { exchange: "viewer" },
    async open(page) {
      await page.goto(`${FAKE}/dashboard/console`);
      await expect(page.getByTestId("omega-console-viewer")).toBeVisible();
    },
  },
  {
    name: "sign-in refused panel",
    mode: { exchange: "refused" },
    async open(page) {
      await page.goto(`${FAKE}/dashboard/console`);
      await expect(page.getByTestId("omega-embed-gate")).toHaveAttribute(
        "data-state",
        "needs-key",
      );
    },
  },
  {
    name: "switcher with inbox count",
    async open(page) {
      await page.goto(`${FAKE}/dashboard/console`);
      await expectConnected(page);
      const frame = await waitForConsoleFrame(page);
      await frame.evaluate(() =>
        window.parent.postMessage(
          { type: "embed.badge", inbox: 2 },
          location.origin,
        ),
      );
      await frame.locator("[data-future-agi]").click();
      await page.getByTestId("omega-product-switcher").click();
      await expect(page.getByTestId("omega-console-inbox-count")).toHaveText(
        "2",
      );
    },
  },
];

test.describe("Console host: axe on Future AGI's document (CP A11Y1)", () => {
  test.skip(!AXE, "AXE_CORE_FILE is not set");
  let host;

  test.beforeEach(async ({ browser, context, request }) => {
    noteBrowser(browser);
    await keepLocal(context);
    host = control(request, FAKE);
  });

  for (const state of STATES) {
    for (const mode of ["light", "dark"]) {
      test(`${state.name}, ${mode}: no serious or critical finding`, async ({
        page,
      }, testInfo) => {
        await host.reset();
        if (state.mode) await host.mode(state.mode);
        await page.goto(`${FAKE}/dashboard`);
        await setHostTheme(page, mode);
        await state.open(page);
        await setHostTheme(page, mode);
        // The route progress bar (#nprogress) fades out after a load; axe runs
        // on the settled page, as a person reads it.
        await expect(page.locator("#nprogress")).toHaveCount(0);
        const violations = await runAxe(page);
        const blocking = violations.filter(
          (v) => v.impact === "serious" || v.impact === "critical",
        );
        writeReceipt(testInfo, `A11Y1 ${state.name} ${mode}`, {
          violations,
          blocking: blocking.length,
        });
        expect(blocking).toEqual([]);
      });
    }
  }
});

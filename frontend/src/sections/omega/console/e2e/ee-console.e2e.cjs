/* eslint-env node */
// The real Future AGI build framing the real EE Console embed build (the EE
// console lane's dist-embed), served on the same site under /omega/console/,
// with a customer-only fixture gateway at /omega/api/*. Runs only when
// CONSOLE_E2E_EE_DIST points at that build.
const { expect, test } = require("playwright/test");

const {
  EE,
  HAS_EE,
  control,
  expectConnected,
  keepLocal,
  msFromFirstHelloTo,
  parentSurfaces,
  pathOf,
  recordFirstHello,
  setHostTheme,
  SHOWN_AT_ONCE_MS,
  waitForConsoleFrame,
} = require("./helpers.cjs");

test.describe("Console frame against the EE Console embed build", () => {
  test.skip(!HAS_EE, "CONSOLE_E2E_EE_DIST is not set");
  let host;

  test.beforeEach(async ({ context, request }) => {
    await keepLocal(context);
    host = control(request, EE);
    await host.reset();
  });

  test("the EE Console opens with Future AGI's key, theme and URL", async ({
    page,
  }) => {
    await page.goto(`${EE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);

    // Its own Fleet-style sidebar, on its default page.
    const nav = frame.getByRole("navigation", { name: "Omega Console" });
    await expect(nav.getByTestId("fx-nav-agents")).toBeVisible();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/chat");
    await expect(frame.locator("html")).toHaveAttribute(
      "data-brand",
      "futureagi",
    );

    // Every Omega call it made carried the assertion Future AGI minted, and
    // only customer /workspace routes were asked for.
    await expect
      .poll(async () => (await host.state()).api.length)
      .toBeGreaterThan(0);
    const server = await host.state();
    expect(
      server.exchanges.every((call) => call.audience === "omega-squad"),
    ).toBe(true);
    for (const call of server.api) {
      expect(server.mintedHashes).toContain(call.keyHash);
      expect(call.path.startsWith("/workspace")).toBe(true);
    }
    const surfaces = await parentSurfaces(page);
    for (const token of server.minted) expect(surfaces).not.toContain(token);
  });

  test("its sidebar drives the Future AGI URL and Back walks it", async ({
    page,
  }) => {
    await page.goto(`${EE}/dashboard/console/agents`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    const nav = frame.getByRole("navigation", { name: "Omega Console" });
    await expect(nav.getByTestId("fx-nav-agents")).toHaveAttribute(
      "aria-current",
      "page",
    );

    // Skills and Explore are hidden until finished (EE shownFeatures.ts):
    // the walk uses a page EE shows.
    await nav.getByTestId("fx-nav-templates").click();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/templates");
    await expect(nav.getByTestId("fx-nav-templates")).toHaveAttribute(
      "aria-current",
      "page",
    );

    await nav.getByTestId("fx-nav-inbox").click();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/inbox");

    await page.goBack();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/templates");
    await expect(nav.getByTestId("fx-nav-templates")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("it follows the Future AGI theme", async ({ page }) => {
    await page.goto(`${EE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);

    await setHostTheme(page, "dark");
    await expect(frame.locator("html")).toHaveAttribute("data-theme", "dark");
    await setHostTheme(page, "light");
    await expect(frame.locator("html")).not.toHaveAttribute(
      "data-theme",
      "dark",
    );
  });

  test("a role without Omega gets its reason at once, and it stays past the Console's own wait", async ({
    page,
  }) => {
    await host.mode({ exchange: "refused" });
    await recordFirstHello(page);
    await page.goto(`${EE}/dashboard/console/agents`);

    const gate = page.getByTestId("omega-embed-gate");
    expect(await msFromFirstHelloTo(page, "needs-key")).toBeLessThan(
      SHOWN_AT_ONCE_MS,
    );
    await expect(gate).toContainText(
      "Omega Console isn't available for your role",
    );
    await expect(page.getByTestId("omega-embed-frame")).toBeHidden();

    // After 6 s the EE Console reports needs-key itself; the reason stays, and
    // its repeated hellos never ask Future AGI for another key.
    await page.waitForTimeout(7_000);
    await expect(gate).toContainText(
      "Omega Console isn't available for your role",
    );
    const refused = await host.state();
    expect(refused.exchanges).toHaveLength(1);
    expect(refused.api).toHaveLength(0);

    await host.mode({ exchange: "ok" });
    await gate.getByRole("button", { name: "Reconnect" }).click();
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await expect(
      frame
        .getByRole("navigation", { name: "Omega Console" })
        .getByTestId("fx-nav-agents"),
    ).toHaveAttribute("aria-current", "page");
    expect((await host.state()).exchanges).toHaveLength(2);
  });

  test("its 'Future AGI' product entry brings the user back", async ({
    page,
  }) => {
    await page.goto(`${EE}/dashboard/console/templates`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);

    await frame.getByRole("button", { name: "Switch product" }).click();
    await frame.getByTestId("fx-product-futureagi").click();

    await expect.poll(() => pathOf(page)).toBe("/dashboard");
    await expect(page.getByTestId("harness-future-agi")).toHaveText(
      "Future AGI page /dashboard",
    );
  });
});

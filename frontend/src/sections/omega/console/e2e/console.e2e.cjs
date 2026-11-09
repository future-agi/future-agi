/* eslint-env node */
// The real Future AGI build framing a fake EE Console (fake-console.cjs) that
// speaks omega-embed/1 the way the EE Console does.
const { expect, test } = require("playwright/test");

const {
  FAKE,
  consoleFrame,
  control,
  expectConnected,
  framePage,
  keepLocal,
  msFromFirstHelloTo,
  parentSurfaces,
  pathOf,
  recordFirstHello,
  setHostTheme,
  SHOWN_AT_ONCE_MS,
  waitForConsoleFrame,
} = require("./helpers.cjs");

const childState = async (page) =>
  (await waitForConsoleFrame(page)).evaluate(() => window.__console);

test.describe("Console frame against a fake Console", () => {
  let host;

  test.beforeEach(async ({ context, request }) => {
    await keepLocal(context);
    host = control(request, FAKE);
    await host.reset();
  });

  test("hands the Console a freshly minted assertion as its key, never through a URL or storage", async ({
    page,
  }) => {
    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);

    // The Console's first route corrects the parent URL in place.
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/chat");
    await expect
      .poll(async () => (await childState(page)).keyHash)
      .not.toBeNull();

    const child = await childState(page);
    const server = await host.state();
    expect(server.exchanges.length).toBeGreaterThanOrEqual(1);
    expect(
      server.exchanges.every((call) => call.audience === "omega-squad"),
    ).toBe(true);
    expect(server.exchanges.every((call) => call.query === "")).toBe(true);
    // The key the Console holds is the last assertion Future AGI minted.
    expect(child.keyHash).toBe(server.mintedHashes.at(-1));
    expect(child.rejected).toEqual([]);

    const surfaces = await parentSurfaces(page);
    for (const token of server.minted) {
      expect(surfaces).not.toContain(token);
    }

    const frame = page.getByTestId("omega-embed-frame");
    await expect(frame).toHaveAttribute("title", "Omega Console");
    await expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    // The Console on this site needs no local-network permission (that is
    // for a Console served from this computer on another origin); only
    // clipboard-write, for its "Copy reference" (lane CONSOLE-HOST, CP P6).
    await expect(frame).toHaveAttribute("allow", "clipboard-write");
    expect(await frame.getAttribute("sandbox")).toBeNull();
    await expect(page).toHaveTitle("Omega Console");
  });

  test("keeps /dashboard/console/<path> and the Console route in step, with Back and reload", async ({
    page,
  }) => {
    await page.goto(`${FAKE}/dashboard/console/agents`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await expect(frame.locator("#title")).toHaveText("Workspace Agents");
    expect(pathOf(page)).toBe("/dashboard/console/agents");

    await frame.locator('[data-nav="/inbox"]').click();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/inbox");

    await frame.locator('[data-nav="/skills"]').click();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/skills");

    // Back walks the Console's pages through the parent URL.
    await page.goBack();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/inbox");
    await expect(frame.locator("#title")).toHaveText("Inbox");
    expect((await childState(page)).navigations).toContain("/inbox");

    // A deep link opens the Console on that page.
    await page.goto(`${FAKE}/dashboard/console/skills/sk1?version=2`);
    await expectConnected(page);
    const deep = await waitForConsoleFrame(page);
    await expect(deep.locator("#route")).toHaveText("/skills/sk1?version=2");

    await page.reload();
    await expectConnected(page);
    await expect(
      (await waitForConsoleFrame(page)).locator("#route"),
    ).toHaveText("/skills/sk1?version=2");
    expect(pathOf(page)).toBe("/dashboard/console/skills/sk1?version=2");
  });

  test("follows the Future AGI theme, also when it changes", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      if (!window.sessionStorage.getItem("e2e-theme-seeded")) {
        window.localStorage.setItem(
          "settings",
          JSON.stringify({ themeMode: "dark" }),
        );
        window.sessionStorage.setItem("e2e-theme-seeded", "1");
      }
    });
    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await expect(frame.locator("html")).toHaveAttribute("data-theme", "dark");

    await setHostTheme(page, "light");
    await expect.poll(async () => (await childState(page)).theme).toBe("light");
    await expect(frame.locator("html")).not.toHaveAttribute(
      "data-theme",
      "dark",
    );

    await setHostTheme(page, "dark");
    await expect.poll(async () => (await childState(page)).theme).toBe("dark");
  });

  test("the Console's 'Future AGI' entry brings the user back to Future AGI", async ({
    page,
  }) => {
    await page.goto(`${FAKE}/dashboard/console/agents`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);

    await frame.getByRole("button", { name: "Future AGI" }).click();

    await expect.poll(() => pathOf(page)).toBe("/dashboard");
    await expect(page.getByTestId("harness-future-agi")).toHaveText(
      "Future AGI page /dashboard",
    );

    // Back returns to the Console page the user left.
    await page.goBack();
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/agents");
    await expectConnected(page);
  });

  test("ignores navigate requests from anything but the keyed Console frame", async ({
    page,
  }) => {
    await page.goto(`${FAKE}/dashboard/console/agents`);
    await expectConnected(page);

    // The page itself, and a sibling frame on the same site, ask to navigate.
    await page.evaluate(() => {
      window.postMessage(
        { type: "embed.navigate", path: "/dashboard/pwned" },
        "*",
      );
      const sibling = document.createElement("iframe");
      sibling.srcdoc =
        "<script>parent.postMessage({type:'embed.navigate',path:'/dashboard/pwned'},'*')</script>";
      document.body.appendChild(sibling);
    });
    // The keyed frame with a path that is not a plain same-site path.
    await consoleFrame(page).evaluate(() => {
      window.parent.postMessage(
        { type: "embed.navigate", path: "//evil.example/x" },
        window.location.origin,
      );
    });
    await page.waitForTimeout(1_000);

    expect(pathOf(page)).toBe("/dashboard/console/agents");
    await expect(page.getByTestId("harness-future-agi")).toHaveCount(0);
    await expectConnected(page);
  });

  test("a role without Omega gets a calm explanation, one exchange, and Reconnect works", async ({
    page,
  }) => {
    await host.mode({ exchange: "refused" });
    await recordFirstHello(page);
    await page.goto(`${FAKE}/dashboard/console`);

    const gate = page.getByTestId("omega-embed-gate");
    // The reason shows as soon as the exchange answers, not after a wait.
    expect(await msFromFirstHelloTo(page, "needs-key")).toBeLessThan(
      SHOWN_AT_ONCE_MS,
    );
    await expect(gate).toContainText(
      "Omega Console isn't available for your role",
    );
    await expect(page.getByTestId("omega-embed-frame")).toBeHidden();
    await expect(gate.getByRole("button", { name: /directly/ })).toHaveCount(0);

    // The Console keeps saying hello; Future AGI does not ask again.
    await expect
      .poll(async () => (await childState(page)).hellos)
      .toBeGreaterThan(3);
    expect((await host.state()).exchanges).toHaveLength(1);

    await host.mode({ exchange: "ok" });
    await gate.getByRole("button", { name: "Reconnect" }).click();
    await expectConnected(page);
    const server = await host.state();
    expect((await childState(page)).keyHash).toBe(server.mintedHashes.at(-1));
  });

  test("Omega not set up on the backend reads as a sign-in problem, not a broken page", async ({
    page,
  }) => {
    await host.mode({ exchange: "unavailable" });
    await recordFirstHello(page);
    await page.goto(`${FAKE}/dashboard/console`);
    const gate = page.getByTestId("omega-embed-gate");
    expect(await msFromFirstHelloTo(page, "needs-key")).toBeLessThan(
      SHOWN_AT_ONCE_MS,
    );
    await expect(gate).toContainText(
      "Omega Console can't sign you in right now",
    );
    await expect(gate.getByRole("button", { name: "Reconnect" })).toBeVisible();
  });

  test("says the Console is unavailable when nothing answers, and recovers on Try again", async ({
    page,
  }) => {
    await host.mode({ consoleUp: false });
    await page.goto(`${FAKE}/dashboard/console`);

    const gate = page.getByTestId("omega-embed-gate");
    await expect(gate).toContainText("Opening Omega Console");
    await expect(gate).toHaveAttribute("data-state", "timeout", {
      timeout: 30_000,
    });
    await expect(gate).toContainText("Omega Console isn't available right now");
    await expect(framePage(page)).toHaveAttribute("data-state", "timeout");

    await host.mode({ consoleUp: true });
    await gate.getByRole("button", { name: "Try again" }).click();
    await expectConnected(page);
  });
});

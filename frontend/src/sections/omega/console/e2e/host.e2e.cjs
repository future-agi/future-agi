/* eslint-env node */
// Lane CONSOLE-HOST (CP F-HOST) in real browsers, against the real Future AGI
// build and the stand-in Console (fake-console.cjs): batched keys (P7), the
// Console's health reports in Future AGI's Sentry (P8, read back from the
// harness's ingest sink), the viewer's panel (P10), the Inbox count in
// Future AGI's switcher and tab title with opt-in notifications (P15), and the
// Hub and remix landings.
const { Buffer } = require("node:buffer");
const { expect, test } = require("playwright/test");

const {
  FAKE,
  control,
  expectConnected,
  keepLocal,
  parentSurfaces,
  pathOf,
  setHostTheme,
  waitForConsoleFrame,
} = require("./helpers.cjs");
const { noteBrowser, shoot, writeReceipt } = require("./evidence.cjs");

const consoleEvents = (state) =>
  state.sentry
    .flatMap((envelope) => envelope.events)
    .filter((event) => event?.tags?.product === "omega-console");
const sentryText = (state) => state.sentry.map((e) => e.text).join("\n");
const ERROR = {
  type: "embed.telemetry",
  kind: "error",
  name: "load",
  routeTemplate: "/agents/:agentId",
  release: "fake-console-1",
  code: "http_502",
  requestId: "req_6f1c2b9e0a7d4c3b8e5f",
};
const TOKEN = `rpl_v1.${Buffer.from(
  JSON.stringify({ c: "conv_8f2a", s: "shr_31c9", exp: 1792500000000 }),
).toString(
  "base64url",
)}.${"Zk9vYmFyLXNpZ25hdHVyZS1ub3QtcmVhbC0wMTIzNDU2Nzg5".slice(0, 43)}`;
const HUB = "futureagi%2Fsupport-triage%401.2.0";

test.describe("Console host (CP F-HOST) with the stand-in Console", () => {
  let host;

  test.beforeEach(async ({ browser, context, request }) => {
    noteBrowser(browser);
    await keepLocal(context);
    host = control(request, FAKE);
    await host.reset();
  });

  test("answers the opening hello's want=4 with one mint and four keys, then a refill with one more", async ({
    page,
  }, testInfo) => {
    await host.mode({ want: 4 });
    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);

    // Every key arrived and its digest is in (the log keeps arrival order).
    const hashed = () =>
      frame.evaluate(
        () => window.__console.keyLog.filter((k) => k.hash !== null).length,
      );
    await expect.poll(hashed).toBe(4);
    const keyLog = await frame.evaluate(() => window.__console.keyLog);
    expect(keyLog.map((k) => k.left)).toEqual([3, 2, 1, null]);
    let state = await host.state();
    expect(state.exchanges.map((e) => e.count)).toEqual([4]);
    for (const key of keyLog) expect(state.mintedHashes).toContain(key.hash);

    // The Console's pool runs low and it asks again: one more exchange call.
    await frame.evaluate(() => window.__askForKeys(3));
    await expect.poll(hashed).toBe(7);
    state = await host.state();
    expect(state.exchanges.map((e) => e.count)).toEqual([4, 3]);
    expect(
      new Set(
        (await frame.evaluate(() => window.__console.keyLog)).map(
          (k) => k.hash,
        ),
      ).size,
    ).toBe(7);

    // No key in the parent's URL, frame src, storage, cookies or DOM.
    const surfaces = await parentSurfaces(page);
    for (const token of state.minted) expect(surfaces).not.toContain(token);
    writeReceipt(testInfo, "P7 batch keys", {
      exchanges: state.exchanges.map(({ count, mode }) => ({ count, mode })),
      keysReceived: 7,
      lefts: keyLog.map((k) => k.left),
      tokenInParentSurfaces: false,
    });
  });

  test("sends a Console error to Future AGI's Sentry once, tagged and with no content; drops product events", async ({
    page,
  }, testInfo) => {
    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await frame.evaluate((error) => {
      const post = (message) =>
        window.parent.postMessage(message, location.origin);
      post({
        ...error,
        message:
          "TypeError: Cannot read properties of undefined (reading 'secret')",
        stack: "at Agent",
      });
      post(error);
      post({
        ...error,
        kind: "event",
        name: "agent_created",
        agent: "Refund bot",
      });
      post({ ...error, routeTemplate: "/agents/agt-7" });
      post({
        type: "embed.telemetry",
        kind: "vital",
        name: "route-data",
        routeTemplate: "/agents",
        release: "fake-console-1",
        valueMs: 412,
      });
    }, ERROR);

    await expect
      .poll(async () => consoleEvents(await host.state()).length, {
        timeout: 20_000,
      })
      .toBeGreaterThan(0);
    // Give a duplicate time to arrive, if one were sent.
    await page.waitForTimeout(3_000);
    const state = await host.state();
    const events = consoleEvents(state);
    expect(events).toHaveLength(1);
    const [event] = events;
    expect(event.message).toBe(
      "Omega Console error (req_6f1c2b9e0a7d4c3b8e5f)",
    );
    expect(event.tags).toMatchObject({
      product: "omega-console",
      "omega.code": "http_502",
      "omega.name": "load",
      "omega.route": "/agents/:agentId",
      "omega.release": "fake-console-1",
      "omega.request_id": "req_6f1c2b9e0a7d4c3b8e5f",
    });
    expect(event.fingerprint).toEqual([
      "omega-console",
      "load",
      "http_502",
      "/agents/:agentId",
    ]);
    const text = sentryText(state);
    // What the Console's message carried besides its closed fields. (Future
    // AGI's own console breadcrumbs are in the envelopes too, as on any page.)
    for (const content of [
      "reading 'secret'",
      "at Agent",
      "agent_created",
      "Refund bot",
      "agt-7",
    ]) {
      expect(text).not.toContain(content);
    }
    writeReceipt(testInfo, "P8 Sentry forwarding", {
      consoleEvents: events.length,
      message: event.message,
      tags: event.tags,
      envelopes: state.sentry.length,
      contentFoundInPayloads: false,
    });
  });

  test("puts the Inbox count in the tab title and in Future AGI's switcher", async ({
    page,
  }, testInfo) => {
    await page.goto(`${FAKE}/dashboard/console`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await frame.evaluate(() =>
      window.parent.postMessage(
        { type: "embed.badge", inbox: 3 },
        location.origin,
      ),
    );
    await expect(page).toHaveTitle("(3) Omega Console");

    // Back to Future AGI through the Console's own entry: the switcher there
    // carries the count the Console last reported.
    await frame.locator("[data-future-agi]").click();
    await expect.poll(() => pathOf(page)).toBe("/dashboard");
    const trigger = page.getByTestId("omega-product-switcher");
    await expect(trigger).toHaveAccessibleName(
      "Switch product (current: Future AGI), 3 approvals waiting in Omega Console",
    );
    await expect(page.getByTestId("omega-console-inbox-dot")).toBeVisible();
    await trigger.click();
    const count = page
      .getByRole("menu", { name: "Products" })
      .getByTestId("omega-console-inbox-count");
    await expect(count).toHaveText("3");
    const files = await shoot(
      page,
      testInfo,
      "P15 switcher with inbox count",
      (mode) => setHostTheme(page, mode),
      async () => {
        if (!(await page.getByRole("menu", { name: "Products" }).isVisible()))
          await trigger.click();
        await expect(count).toBeVisible();
      },
    );
    writeReceipt(testInfo, "P15 inbox count", {
      title: "(3) Omega Console",
      switcherLabel: await trigger.getAttribute("aria-label"),
      screenshots: files,
    });
  });

  test("shows a viewer the panel with no frame, and opens the Console after Check again", async ({
    page,
  }, testInfo) => {
    await host.mode({ exchange: "viewer" });
    await page.goto(`${FAKE}/dashboard/console`);
    const panel = page.getByTestId("omega-console-viewer");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("omega-embed-frame")).toHaveCount(0);
    await expect(
      page.getByText("Viewers can't use Omega Console"),
    ).toBeVisible();
    // The way back to Future AGI stays on screen, and says the same.
    await page.getByTestId("omega-product-switcher").click();
    await expect(
      page.getByTestId("omega-product-detail-omega-console"),
    ).toHaveText("Ask an admin for access");
    await expect(page.getByTestId("omega-console-notify")).toHaveCount(0);
    await page.keyboard.press("Escape");
    const files = await shoot(
      page,
      testInfo,
      "P10 viewer panel",
      (mode) => setHostTheme(page, mode),
      async () => {
        await expect(panel).toBeVisible();
      },
    );

    let state = await host.state();
    const viewerCalls = state.exchanges.length;
    await page.getByRole("button", { name: "Check again" }).click();
    await expect(page.getByTestId("omega-console-viewer-note")).toHaveText(
      "You're still a viewer in this workspace.",
    );
    await host.mode({ exchange: "ok" });
    await page.getByRole("button", { name: "Check again" }).click();
    await expectConnected(page);
    state = await host.state();
    writeReceipt(testInfo, "P10 viewer", {
      frameCreatedForViewer: false,
      exchangeCallsBeforeCheck: viewerCalls,
      exchangeCallsAfter: state.exchanges.length,
      reopened: true,
      screenshots: files,
    });
  });

  test("hands a Hub template to the Console's import route only while the handoff is on", async ({
    page,
  }, testInfo) => {
    await host.mode({ config: { VITE_OMEGA_HUB_CONSOLE: "1" } });
    await page.goto(`${FAKE}/dashboard/console?hub=${HUB}`);
    await expectConnected(page);
    await expect
      .poll(() => pathOf(page))
      .toBe(`/dashboard/console/templates/import?hub=${HUB}`);
    const frame = await waitForConsoleFrame(page);
    await expect
      .poll(() => frame.evaluate(() => window.__console.route))
      .toBe(`/templates/import?hub=${HUB}`);

    await host.mode({ config: {} });
    await page.goto(`${FAKE}/dashboard/console?hub=${HUB}`);
    await expectConnected(page);
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/chat");
    writeReceipt(testInfo, "Hub handoff", {
      on: `/templates/import?hub=${HUB}`,
      off: "/chat (the Console home)",
    });
  });

  test("keeps a remix link only while remix is on, and its token never reaches Sentry", async ({
    page,
  }, testInfo) => {
    await host.mode({ config: { VITE_OMEGA_CONSOLE_REMIX: "1" } });
    await page.goto(`${FAKE}/dashboard/console/remix/${TOKEN}`);
    await expectConnected(page);
    const frame = await waitForConsoleFrame(page);
    await expect
      .poll(() => frame.evaluate(() => window.__console.route))
      .toBe(`/remix/${TOKEN}`);
    // The page load is a Sentry transaction named by its URL.
    await expect
      .poll(
        async () =>
          (await host.state()).sentry.some((e) => e.transactions.length > 0),
        { timeout: 30_000 },
      )
      .toBe(true);
    let state = await host.state();
    expect(sentryText(state)).not.toContain("rpl_v1");

    await host.mode({ config: {} });
    await page.goto(`${FAKE}/dashboard/console/remix/${TOKEN}`);
    await expectConnected(page);
    await expect.poll(() => pathOf(page)).toBe("/dashboard/console/chat");
    state = await host.state();
    expect(sentryText(state)).not.toContain("rpl_v1");
    writeReceipt(testInfo, "Remix landing", {
      on: "/remix/<token> reached the Console",
      off: "landed on the Console home",
      envelopes: state.sentry.length,
      tokenInSentryPayloads: false,
    });
  });
});

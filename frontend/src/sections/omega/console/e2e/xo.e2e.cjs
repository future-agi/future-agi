/* eslint-env node */
// CP P6 and SEC5 in real browsers: the real Future AGI build frames the real
// EE Console (ee/p-console-shell, packaged for its own origin) served by
// Future AGI's own Console-origin listener code, on another port of the same
// host (same site, another origin). Runs only when CONSOLE_E2E_XO_ORIGIN_DIR
// and CONSOLE_E2E_EE_FIXTURES are set (see README.md).
const { expect, test } = require("playwright/test");

const { FAKE, HAS_XO, XO, control, keepLocal } = require("./helpers.cjs");
const { noteBrowser, writeReceipt } = require("./evidence.cjs");

const origin = (request) => ({
  reset: () => request.post(`${XO.CONSOLE}/__e2e/origin-reset`),
  fault: (fault) =>
    request.post(`${XO.CONSOLE}/__e2e/origin-fault`, { data: fault }),
  state: async () =>
    (await request.get(`${XO.CONSOLE}/__e2e/origin-state`)).json(),
});
const SENTINEL = "fa-session-sentinel-6d2f";

const consoleFrame = (page) =>
  page
    .frames()
    .find((frame) => frame.url().startsWith(`${XO.CONSOLE}/omega/console/`));

async function openConsole(page, path = "/dashboard/console/agents") {
  await page.addInitScript((sentinel) => {
    if (window.top === window && window.location.port === "43310") {
      window.localStorage.setItem("accessToken", sentinel);
    }
  }, SENTINEL);
  await page.goto(`${XO.HOST}${path}`);
  await expect(page.getByTestId("omega-tab-console")).toHaveAttribute(
    "data-state",
    "connected",
    { timeout: 30_000 },
  );
  await expect.poll(() => Boolean(consoleFrame(page))).toBe(true);
  return consoleFrame(page);
}

test.describe("The Console on its own origin (CP P6, SEC5)", () => {
  test.skip(
    !HAS_XO,
    "CONSOLE_E2E_XO_ORIGIN_DIR and CONSOLE_E2E_EE_FIXTURES are not set",
  );
  let host;
  let console;
  // The build's Sentry DSN is the fake host's sink, whichever host serves it.
  let sentry;

  test.beforeEach(async ({ browser, context, request }) => {
    noteBrowser(browser);
    await keepLocal(context);
    host = control(request, XO.HOST);
    console = origin(request);
    sentry = control(request, FAKE);
    await host.reset();
    await console.reset();
    await sentry.reset();
  });

  test("opens sandboxed with Future AGI's keys, and the frame cannot reach Future AGI's storage or move it", async ({
    page,
  }, testInfo) => {
    const frame = await openConsole(page);
    const element = page.getByTestId("omega-embed-frame");
    await expect(element).toHaveAttribute("sandbox", /allow-same-origin/);
    expect(await element.getAttribute("sandbox")).not.toContain(
      "allow-top-navigation",
    );
    await expect(element).toHaveAttribute("allow", /clipboard-write/);
    await expect(page.getByTestId("omega-tab-console")).toHaveAttribute(
      "data-frame-origin",
      "console",
    );

    // The real EE Console works under the sandbox: its nav and its data.
    const nav = frame.getByRole("navigation", { name: "Omega Console" });
    await expect(nav.getByTestId("fx-nav-agents")).toBeVisible();
    // Skills and Explore are hidden until finished (EE shownFeatures.ts), so
    // the walk uses a page EE shows.
    await nav.getByTestId("fx-nav-templates").click();
    await expect
      .poll(() => new URL(page.url()).pathname)
      .toBe("/dashboard/console/templates");

    const probe = await frame.evaluate(() => {
      const attempt = (fn) => {
        try {
          return { ok: true, value: String(fn()) };
        } catch (error) {
          return { ok: false, error: error.name };
        }
      };
      return {
        origin: window.location.origin,
        parentLocalStorage: attempt(() =>
          window.parent.localStorage.getItem("accessToken"),
        ),
        parentDocument: attempt(() => window.parent.document.cookie),
        topNavigation: attempt(() => {
          window.top.location.href = "http://127.0.0.1:43312/escaped";
          return "assigned";
        }),
      };
    });
    expect(probe.origin).toBe(XO.CONSOLE);
    expect(probe.parentLocalStorage.ok).toBe(false);
    expect(probe.parentDocument.ok).toBe(false);
    await page.waitForTimeout(1_000);
    expect(new URL(page.url()).origin).toBe(XO.HOST);
    expect(
      await page.evaluate(() => window.localStorage.getItem("accessToken")),
    ).toBe(SENTINEL);

    // Every API call carried a key the host minted, and none was used twice.
    const api = (await console.state()).api;
    expect(api.length).toBeGreaterThan(0);
    expect(api.every((call) => call.known && !call.replayed)).toBe(true);
    expect(new Set(api.map((call) => call.keyHash)).size).toBe(api.length);
    const exchanges = (await host.state()).exchanges;
    writeReceipt(testInfo, "P6 own origin", {
      frameSandbox: await element.getAttribute("sandbox"),
      frameAllow: await element.getAttribute("allow"),
      probe,
      hostStillAt: new URL(page.url()).origin,
      apiCalls: api.length,
      exchangeCalls: exchanges.length,
      exchangeCounts: exchanges.map((e) => e.count),
      keysUsedOnce: true,
    });
  });

  test("ignores a hello from a frame on another origin, and mints nothing for it", async ({
    page,
  }, testInfo) => {
    await openConsole(page);
    const before = (await host.state()).exchanges.length;
    await page.evaluate((src) => {
      const iframe = document.createElement("iframe");
      iframe.src = src;
      iframe.id = "foreign";
      document.body.appendChild(iframe);
    }, `${XO.FOREIGN}/hello.html`);
    const foreignFrame = () =>
      page.frames().find((f) => f.url().startsWith(XO.FOREIGN));
    await expect.poll(() => Boolean(foreignFrame())).toBe(true);
    const foreign = foreignFrame();
    await page.waitForTimeout(2_500);
    const received = await foreign.evaluate(() => window.__received);
    const after = (await host.state()).exchanges.length;
    expect(received).toEqual([]);
    expect(after).toBe(before);
    writeReceipt(testInfo, "SEC5 foreign hello", {
      hellosSent: 10,
      keysReceived: received.length,
      extraExchangeCalls: after - before,
    });
  });

  test("refuses to be framed by another origin, while Future AGI frames it", async ({
    page,
  }, testInfo) => {
    await page.goto(`${XO.FOREIGN}/frame-console.html`);
    await page.waitForTimeout(6_000);
    const messages = await page.evaluate(() => window.__messages);
    const hellos = messages.filter((m) => m.type === "embed.hello");
    expect(hellos).toEqual([]);
    // Positive control: the same Console says hello to Future AGI.
    const control2 = await page.context().newPage();
    await openConsole(control2);
    const csp = (await console.state()).csp;
    writeReceipt(testInfo, "SEC5 framing refused", {
      foreignHellos: hellos.length,
      futureAgiConnected: true,
      cspReports: csp,
    });
    await control2.close();
  });

  test("a malformed answer becomes one Sentry event carrying that answer's request id", async ({
    page,
  }, testInfo) => {
    const frame = await openConsole(page, "/dashboard/console/templates");
    const nav = frame.getByRole("navigation", { name: "Omega Console" });
    // Skills is hidden until finished (EE shownFeatures.ts); Integrations is
    // shown and reads its catalog the same way.
    await console.fault({ path: "/workspace/integrations", times: 3 });
    await nav.getByTestId("fx-nav-integrations").click();
    const reference = frame.getByTestId("fx-error-reference").first();
    await expect(reference).toBeVisible({ timeout: 20_000 });
    const shownId = (await reference.locator("code").textContent()).trim();
    const consoleEvents = async () =>
      (await sentry.state()).sentry
        .flatMap((e) => e.events)
        .filter((e) => e?.tags?.product === "omega-console");
    await expect
      .poll(async () => (await consoleEvents()).length, { timeout: 20_000 })
      .toBeGreaterThan(0);
    await page.waitForTimeout(3_000);
    const served = (await console.state()).served;
    const events = await consoleEvents();
    const forThis = events.filter(
      (e) => e.tags["omega.request_id"] === shownId,
    );
    expect(served).toContain(shownId);
    expect(forThis).toHaveLength(1);
    expect(forThis[0].tags).toMatchObject({
      product: "omega-console",
      "omega.route": "/integrations",
    });
    // Nothing of the workspace's content travels in any Sentry payload.
    const text = (await sentry.state()).sentry.map((e) => e.text).join("\n");
    expect(text).not.toMatch(/Acme|refund|zeta|invoi/i);
    // The sidebar still works after the failure.
    await console.fault(null);
    await nav.getByTestId("fx-nav-agents").click();
    await expect
      .poll(() => new URL(page.url()).pathname)
      .toBe("/dashboard/console/agents");
    writeReceipt(testInfo, "P8 malformed answer to Sentry", {
      shownReference: shownId,
      faultedRequestIds: served,
      sentryEvents: events.map((e) => ({ message: e.message, tags: e.tags })),
    });
  });

  test("no CSP violation is enforced on the Console origin across these pages", async ({
    page,
  }, testInfo) => {
    const frame = await openConsole(page, "/dashboard/console/chat");
    const nav = frame.getByRole("navigation", { name: "Omega Console" });
    for (const id of [
      "fx-nav-agents",
      "fx-nav-templates",
      "fx-nav-integrations",
      "fx-nav-usage",
      "fx-nav-settings",
    ]) {
      const link = nav.getByTestId(id);
      if (await link.count()) await link.click();
    }
    await page.waitForTimeout(2_000);
    const csp = (await console.state()).csp;
    const enforced = csp.filter((report) => report.disposition !== "report");
    expect(enforced).toEqual([]);
    writeReceipt(testInfo, "P6 CSP", {
      reports: csp,
      enforced: enforced.length,
      trustedTypes: (await console.state()).consoleConfig.trustedTypes,
    });
  });
});

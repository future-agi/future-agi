/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// embed-host end to end: the real Future AGI build (index.html, main.jsx,
// app.jsx and every analytics SDK) framing a fake omega-embed/1 child on a
// second loopback origin. See serve-harness.cjs and child-server.cjs.
const { test, expect } = require("playwright/test");

const {
  CHILD,
  CONTROL,
  PARENT,
  THIRD,
  gate,
  link,
  newToken,
  sha256,
  surface,
  waitForFrame,
} = require("./helpers.cjs");
const { analyticsRecords, attachSink, recordContains } = require("./sink.cjs");

// The child's host as it would appear in a leaked URL, raw and encoded.
const CHILD_HOST = new URL(CHILD).host;
const CHILD_HOST_NEEDLES = [CHILD_HOST, encodeURIComponent(CHILD_HOST)];

// Waits until the SDKs have actually sent something, so a clean result is not
// clean only because nothing was sent yet.
async function waitForAnalytics(records) {
  await expect
    .poll(
      () => {
        const sent = analyticsRecords(records);
        const gtm = sent.some((r) =>
          r.url.includes("google-analytics.com/g/collect"),
        );
        const posthog = sent.some(
          (r) =>
            /posthog\.com\/(?:e|i\/v0\/e|batch)\b/.test(r.url) &&
            r.body?.length,
        );
        return gtm && posthog;
      },
      {
        timeout: 30_000,
        message: "GTM and PostHog should both have sent events",
      },
    )
    .toBe(true);
}

const leaksIn = (records, needles) =>
  records
    .filter((record) =>
      needles.some((needle) => recordContains(record, needle)),
    )
    .map((record) => record.url);

test.describe("omega-embed/1 host", () => {
  test("handshake, route and theme sync; the token stays out of URL, storage and network", async ({
    page,
    context,
  }) => {
    const token = newToken();
    const records = await attachSink(context, { mainPage: () => page });
    await page.emulateMedia({ colorScheme: "light" });

    await page.goto(link(PARENT, CHILD, token));

    await expect(surface(page)).toHaveAttribute("data-state", "connected");
    const child = await waitForFrame(page, `${CHILD}/child.html`);

    // The child got exactly the key from the opened link, over postMessage.
    await expect
      .poll(() => child.evaluate(() => window.__embed.keyHash))
      .toBe(sha256(token));
    expect(await child.evaluate(() => window.__embed.received[0])).toBe(
      "embed.key",
    );
    expect(child.url()).not.toContain(token);

    // The address bar, history and storage never held the token.
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);
    const surfaces = await page.evaluate(() => ({
      href: window.location.href,
      history: JSON.stringify(window.history.state),
      local: JSON.stringify({ ...window.localStorage }),
      session: JSON.stringify({ ...window.sessionStorage }),
      cookie: document.cookie,
      name: window.name,
      intakeGlobal: window.__omegaConnect,
      enumerable: Object.keys(window).includes("__omegaConnect"),
    }));
    for (const [where, value] of Object.entries(surfaces)) {
      expect(String(value), `token in ${where}`).not.toContain(token);
    }
    expect(surfaces.intakeGlobal ?? null).toBeNull();
    expect(surfaces.enumerable).toBe(false);

    // Frame attributes.
    const frameAttrs = await page
      .getByTestId("omega-embed-frame")
      .evaluate((el) => ({
        sandbox: el.hasAttribute("sandbox"),
        allow: el.getAttribute("allow"),
        referrerPolicy: el.getAttribute("referrerpolicy"),
        className: el.className,
        src: el.getAttribute("src"),
      }));
    expect(frameAttrs).toMatchObject({
      sandbox: false,
      allow: "local-network-access; loopback-network",
      referrerPolicy: "no-referrer",
    });
    expect(frameAttrs.className).toContain("ph-no-capture rr-block");
    expect(frameAttrs.src).not.toContain(token);

    // Initial route travelled in the frame URL; theme follows the host.
    expect(await child.evaluate(() => window.__embed.route)).toBe("/runs/1");
    await expect
      .poll(() => child.evaluate(() => window.__embed.theme))
      .toBe("light");
    await page.emulateMedia({ colorScheme: "dark" });
    await expect
      .poll(() => child.evaluate(() => window.__embed.theme))
      .toBe("dark");

    // Child -> parent route.
    await child.click('[data-go="/runs/2"]');
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/2`);
    // Parent -> child route (Back).
    await page.goBack();
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);
    await expect
      .poll(() => child.evaluate(() => window.__embed.route))
      .toBe("/runs/1");
    expect(await child.evaluate(() => window.__embed.navigations)).toContain(
      "/runs/1",
    );

    // A third origin on the page cannot drive the parent or get a key.
    await page.evaluate((src) => {
      const el = document.createElement("iframe");
      el.src = src;
      el.style.cssText =
        "position:fixed;width:1px;height:1px;bottom:0;right:0;border:0";
      document.body.appendChild(el);
    }, `${THIRD}/third.html`);
    const third = await waitForFrame(page, `${THIRD}/third.html`);
    await page.waitForTimeout(1500);
    expect(await third.evaluate(() => window.__received)).toEqual([]);
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);

    // embed.open: another site is ignored; Future AGI's own origin opens.
    await child.click('[data-open="https://evil.example/phish"]');
    await page.waitForTimeout(500);
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);

    await waitForAnalytics(records);

    // A second link opened in the same tab is a fragment navigation, not a
    // page load: it must be taken out of the address bar just the same.
    const second = newToken();
    await page.goto(link(PARENT, CHILD, second));
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);
    expect(await page.evaluate(() => window.location.href)).not.toContain(
      second,
    );

    await child.click('[data-open^="/dashboard/observe"]');
    await expect(page).toHaveURL(`${PARENT}/dashboard/observe/p1?from=studio`);
    await expect(page.getByTestId("harness-elsewhere")).toBeVisible();

    // Network: the token is in no request at all, analytics never carry the
    // fragment or the child's origin, and the parent never calls the child.
    expect(leaksIn(records, [token, second])).toEqual([]);
    expect(
      leaksIn(analyticsRecords(records), [
        "#connect",
        "%23connect",
        ...CHILD_HOST_NEEDLES,
      ]),
    ).toEqual([]);
    const parentToChild = records.filter(
      (r) => r.isMainFrame && r.url.startsWith(CHILD),
    );
    expect(parentToChild.map((r) => r.url)).toEqual([]);
    console.log(
      `[sink] ${records.length} requests, ${analyticsRecords(records).length} analytics: ` +
        [
          ...new Set(
            analyticsRecords(records).map((r) => new URL(r.url).hostname),
          ),
        ].join(", "),
    );
  });

  test("ignores a hello from the frame once its document is on another origin", async ({
    page,
    context,
  }) => {
    const token = newToken();
    const records = await attachSink(context, { mainPage: () => page });

    await page.goto(
      link(PARENT, `${CHILD}/evil-redirect`, token, "/dashboard/studio"),
    );

    const evil = await waitForFrame(page, `${THIRD}/evil.html`);
    await page.waitForTimeout(1500); // the page shouts three times within 900 ms
    expect(await evil.evaluate(() => window.__received)).toEqual([]);
    await expect(surface(page)).toHaveAttribute("data-state", "connecting");
    await expect(page).toHaveURL(`${PARENT}/dashboard/studio`);
    expect(leaksIn(records, [token])).toEqual([]);
  });

  test("explains a server that will not be framed, within 6 s, and opens it directly", async ({
    page,
    context,
  }) => {
    const token = newToken();
    await attachSink(context, { mainPage: () => page });
    await page.goto(
      link(PARENT, `${CHILD}/framing-blocked`, token, "/dashboard/studio"),
    );

    const started = Date.now();
    await expect(gate(page)).toHaveAttribute("data-state", "timeout", {
      timeout: 15_000,
    });
    expect(Date.now() - started).toBeGreaterThanOrEqual(5_000);
    await expect(gate(page)).toHaveAttribute("data-cause", "framing-blocked");
    await expect(page.getByText("didn't open inside Future AGI")).toBeVisible();

    const [popup] = await Promise.all([
      context.waitForEvent("page"),
      page.getByRole("button", { name: "Open local Studio directly" }).click(),
    ]);
    await popup.waitForLoadState("domcontentloaded");
    expect(popup.url()).toBe(`${CHILD}/framing-blocked/child.html`);
    expect(await popup.evaluate(() => window.opener)).toBeNull();
  });

  test("says nothing is answering when the server is not running", async ({
    page,
    context,
  }) => {
    await attachSink(context, { mainPage: () => page });
    await page.goto(
      link(PARENT, "http://127.0.0.1:4199", newToken(), "/dashboard/studio"),
    );

    await expect(gate(page)).toHaveAttribute("data-state", "timeout", {
      timeout: 15_000,
    });
    await expect(gate(page)).toHaveAttribute("data-cause", "not-running");
    await expect(
      page.getByText("Can't reach Studio at http://127.0.0.1:4199"),
    ).toBeVisible();
  });

  for (const [variant, state, text] of [
    ["refused", "refused", "Local Studio refused the connection"],
    ["needs-key", "needs-key", "Local Studio needs to be reconnected"],
  ]) {
    test(`shows the child's ${variant} gate`, async ({ page, context }) => {
      await attachSink(context, { mainPage: () => page });
      await page.goto(
        link(PARENT, `${CHILD}/${variant}`, newToken(), "/dashboard/studio"),
      );
      await expect(gate(page)).toHaveAttribute("data-state", state);
      await expect(page.getByText(text)).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Reconnect" }),
      ).toBeVisible();
    });
  }
});

test.describe("analytics sink, negative control", () => {
  test("with the guard switched off, the same sink captures the token", async ({
    page,
    context,
  }) => {
    const token = newToken();
    const records = await attachSink(context, { mainPage: () => page });

    await page.goto(link(CONTROL, CHILD, token));
    // Without the intake the secret stays in the address bar...
    expect(page.url()).toContain(`t=${token}`);
    await waitForAnalytics(records);

    // ...and reaches analytics, which this sink sees.
    const sent = analyticsRecords(records);
    expect(leaksIn(sent, [token]).length).toBeGreaterThan(0);
    expect(leaksIn(sent, ["#connect", "%23connect"]).length).toBeGreaterThan(0);
    expect(leaksIn(sent, CHILD_HOST_NEEDLES).length).toBeGreaterThan(0);
  });
});

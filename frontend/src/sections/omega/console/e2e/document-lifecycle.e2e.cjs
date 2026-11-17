/* eslint-env node */
// Full host build with either its protocol stand-in or the actual EE embed
// build. Hold a real subresource, not a synthetic iframe load/message event.
const { expect, test } = require("playwright/test");

const {
  EE,
  FAKE,
  HAS_EE,
  control,
  expectConnected,
  framePage,
  keepLocal,
  waitForConsoleFrame,
} = require("./helpers.cjs");

async function observeLifecycle(page) {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    // Only counters and gate enums escape this observer. Never retain or
    // return a doc, challenge, token or whole protocol message to the runner.
    const seen = { ready: 0, proofs: 0, readyProofs: 0, loads: 0 };
    window.__embedLifecycle = seen;
    window.addEventListener("message", (event) => {
      const frame = document.querySelector('[data-testid="omega-embed-frame"]');
      if (
        event.source !== frame?.contentWindow ||
        event.origin !== location.origin
      ) {
        return;
      }
      if (event.data?.type === "embed.gate" && event.data.state === "ready") {
        seen.ready += 1;
      }
      if (event.data?.type === "embed.proof") {
        seen.proofs += 1;
        if (event.data.state === "ready") seen.readyProofs += 1;
      }
    });
    // A load event's path stops at Document; it never reaches Window.
    // Capture here because iframe load events do not bubble.
    document.addEventListener(
      "load",
      (event) => {
        const frame = event.target;
        if (
          frame?.dataset?.testid === "omega-embed-frame" &&
          frame.contentWindow.location.pathname.startsWith("/omega/console/")
        ) {
          seen.loads += 1;
        }
      },
      true,
    );
  });
}
const lifecycle = (page) => page.evaluate(() => window.__embedLifecycle);

async function holdNextLoad(page, base) {
  const resource = `${base}/__e2e/held-console-load.png`;
  let held;
  await page.route(resource, (route) => {
    held = route;
  });
  await page.route(
    (url) => url.origin === base && url.pathname === "/omega/console/",
    async (route) => {
      const response = await route.fetch();
      const html = await response.text();
      expect(html).toContain("</body>");
      await route.fulfill({
        response,
        body: html.replace("</body>", `<img alt="" src="${resource}"></body>`),
      });
    },
    { times: 1 },
  );
  return async () => {
    await expect.poll(() => Boolean(held)).toBe(true);
    // An in-place replacement cancels the predecessor's held request.
    if (!held.request().failure()) await held.fulfill({ status: 204, body: "" });
  };
}

test.describe("Console lifecycle observer", () => {
  test("counts exactly one load for a plain same-origin iframe navigation", async ({ context, page }) => {
    await keepLocal(context);
    await observeLifecycle(page);
    await page.route(
      (url) =>
        url.origin === FAKE &&
        ["/__e2e/lifecycle-observer.html", "/omega/console/"].includes(url.pathname),
      (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<!doctype html><html><body></body></html>",
        }),
    );
    await page.goto(`${FAKE}/__e2e/lifecycle-observer.html`);
    expect(await lifecycle(page)).toEqual({
      ready: 0,
      proofs: 0,
      readyProofs: 0,
      loads: 0,
    });

    // No protocol scripts or synthetic events: wait for the actual element's
    // load so the final equality also rejects duplicate observer counts.
    await page.evaluate(
      () =>
        new Promise((resolve) => {
          const frame = document.createElement("iframe");
          frame.dataset.testid = "omega-embed-frame";
          frame.src = "/omega/console/?observer=1";
          frame.addEventListener("load", () => resolve(), { once: true });
          document.body.append(frame);
        }),
    );
    expect(await lifecycle(page)).toEqual({
      ready: 0,
      proofs: 0,
      readyProofs: 0,
      loads: 1,
    });
  });
});

for (const { name, base, enabled } of [
  { name: "protocol stand-in", base: FAKE, enabled: true },
  { name: "real EE Console", base: EE, enabled: HAS_EE },
]) {
  test.describe(`Console document lifecycle with ${name}`, () => {
    test.beforeAll(() => {
      expect(
        enabled,
        "CONSOLE_E2E_EE_DIST must supply the paired probe-aware EE build",
      ).toBe(true);
    });
    let host;
    test.beforeEach(async ({ context, request, page }) => {
      await keepLocal(context);
      host = control(request, base);
      await host.reset();
      await observeLifecycle(page);
    });

    test("authenticates before a delayed load, then resumes from live proof without a second ready", async ({ page }) => {
      const release = await holdNextLoad(page, base);
      await page.goto(`${base}/dashboard/console`, {
        waitUntil: "domcontentloaded",
      });
      await expectConnected(page);
      const frame = await waitForConsoleFrame(page);
      await expect
        .poll(() => frame.evaluate(() => document.readyState))
        .toBe("interactive");
      expect((await lifecycle(page)).loads).toBe(0);
      expect((await lifecycle(page)).ready).toBe(1);

      // The child's authentication deadline must not wait on an unrelated
      // resource. Its actual ready phase survives this six-second delay.
      await page.waitForTimeout(6_500);
      await expectConnected(page);
      await release();
      await expect
        .poll(async () => (await lifecycle(page)).readyProofs)
        .toBe(1);
      await expectConnected(page);
      // Wait for the browser load observation as well as the async proof reply.
      await expect.poll(async () => (await lifecycle(page)).loads).toBe(1);
      expect((await lifecycle(page)).ready).toBe(1);
      expect(await waitForConsoleFrame(page)).toBe(frame);
    });

    test("does not inherit A's ready when a silent document replaces A before its load", async ({ page }) => {
      const release = await holdNextLoad(page, base);
      await page.goto(`${base}/dashboard/console`, {
        waitUntil: "domcontentloaded",
      });
      await expectConnected(page);
      const frame = await waitForConsoleFrame(page);
      expect((await lifecycle(page)).loads).toBe(0);
      const exchanges = (await host.state()).exchanges.length;
      const element = await page
        .getByTestId("omega-embed-frame")
        .elementHandle();
      // No protocol script at all: B cannot answer the fresh probe or claim
      // A's authentication. The iframe element and WindowProxy are unchanged.
      await page.route(
        (url) => url.origin === base && url.pathname === "/omega/console/",
        (route) =>
          route.fulfill({
            contentType: "text/html",
            body: "<!doctype html><html><body></body></html>",
          }),
        { times: 1 },
      );
      await frame.goto(`${base}/omega/console/?embed=1`);
      await expect.poll(async () => (await lifecycle(page)).loads).toBe(1);
      await expect(framePage(page)).toHaveAttribute("data-state", "connecting");
      await expect(page.getByTestId("omega-embed-frame")).toBeHidden();
      await page.waitForTimeout(500);
      await expect(framePage(page)).not.toHaveAttribute(
        "data-state",
        "connected",
      );
      expect((await lifecycle(page)).proofs).toBe(0);
      expect((await host.state()).exchanges).toHaveLength(exchanges);
      expect(
        await element.evaluate(
          (node) =>
            node === document.querySelector('[data-testid="omega-embed-frame"]'),
        ),
      ).toBe(true);
      await release();
    });

    test("a failed load-proof mint recovers on a successful refill with no fabricated second ready", async ({ page }) => {
      await recoverProofMint(page, base, host);
    });
  });
}

async function recoverProofMint(page, base, host) {
  const release = await holdNextLoad(page, base);
  await page.goto(`${base}/dashboard/console`, {
    waitUntil: "domcontentloaded",
  });
  await expectConnected(page);
  const frame = await waitForConsoleFrame(page);
  expect((await lifecycle(page)).ready).toBe(1);
  await host.mode({ exchange: "unavailable" });
  await release();
  await expect.poll(async () => (await lifecycle(page)).readyProofs).toBe(1);
  await expect(page.getByTestId("omega-embed-gate")).toHaveAttribute(
    "data-state",
    "needs-key",
  );
  expect((await host.state()).exchanges.at(-1).mode).toBe("unavailable");
  const attempts = (await host.state()).exchanges.length;

  await host.mode({ exchange: "ok" });
  if (base === FAKE) {
    await page.waitForTimeout(2_100); // the real useConsoleKey transient backoff
    await frame.evaluate(() => window.__askForKeys(4));
  } else {
    // Let the real child's opaque pooled keys age past OPAQUE_KEY_USE_MS
    // (25 s). Its own router/read/key queue must request the refill; do not
    // inject a synthetic hello, proof or second ready into the real pair.
    await page.waitForTimeout(26_000);
    await frame.getByTestId("fx-nav-agents").dispatchEvent("click");
  }
  await expect
    .poll(async () =>
      (await host.state()).exchanges
        .slice(attempts)
        .some((call) => call.mode === "ok"),
    )
    .toBe(true);
  await expectConnected(page);
  expect((await lifecycle(page)).ready).toBe(1);
  expect((await lifecycle(page)).proofs).toBe(1);
  expect(await waitForConsoleFrame(page)).toBe(frame);
}

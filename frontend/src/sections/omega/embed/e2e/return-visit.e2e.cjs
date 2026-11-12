/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// RC1/A06: a return visit or child reload loses the memory-only key. The
// host's one-shot token is spent, so needs-key remains until fresh intake.
// Every animation frame of the parent is sampled to catch false connected
// states on a remount.
const { test, expect } = require("playwright/test");

const {
  CHILD,
  PARENT,
  gate,
  link,
  newToken,
  sha256,
  surface,
  waitForFrame,
} = require("./helpers.cjs");
const { attachSink } = require("./sink.cjs");
const { KEY_CHECK_MS, KEY_WAIT_MS } = require("./studio-like-child.cjs");

// Records the surface's state on every animation frame of the top page, from
// the first frame on (an init script, so the first visit is sampled too).
function samplePaintedStates() {
  if (window.top !== window) return;
  const samples = [];
  window.__paint = samples;
  const tick = () => {
    const el = document.querySelector('[data-testid="omega-embedded-surface"]');
    const state = el ? el.dataset.state : "none";
    const last = samples[samples.length - 1];
    if (last && last.state === state) last.frames += 1;
    else samples.push({ state, at: performance.now(), frames: 1 });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

const paintedSince = (page, since) =>
  page.evaluate(
    (t) =>
      window.__paint.filter(
        (s, i, all) => s.at >= t || (all[i + 1] ? all[i + 1].at > t : true),
      ),
    since,
  );

async function visitThenComeBack(page, variant, token) {
  await page.addInitScript(samplePaintedStates);
  await page.goto(link(PARENT, `${CHILD}/${variant}`, token));
  await expect(surface(page)).toHaveAttribute("data-state", "connected");
  const first = await waitForFrame(page, `${CHILD}/${variant}/child.html`);
  await expect
    .poll(() => first.evaluate(() => window.__studio.gates))
    .toEqual(["ready"]);
  expect(await first.evaluate(() => window.__studio.keyHash)).toBe(
    sha256(token),
  );

  // Leave the Studio tab through the child's own link, then come back.
  await first.click('[data-open^="/dashboard/observe"]');
  await expect(page.getByTestId("harness-elsewhere")).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => window.__paint.at(-1)?.state))
    .toBe("none");
  const leftAt = await page.evaluate(() => performance.now());
  await page.goBack();
  await expect(page).toHaveURL(`${PARENT}/dashboard/studio/runs/1`);
  const again = await waitForFrame(page, `${CHILD}/${variant}/child.html`);
  return { leftAt, again };
}

test.describe("return visit to a framed Studio", () => {
  test("a remounted child needs fresh intake, never a recovered key", async ({
    page,
    context,
  }) => {
    const token = newToken();
    await attachSink(context, { mainPage: () => page });
    const { leftAt, again } = await visitThenComeBack(
      page,
      "studio-like",
      token,
    );
    await expect(gate(page)).toHaveAttribute("data-state", "needs-key", {
      timeout: 15_000,
    });
    const studio = await again.evaluate(() => window.__studio);
    expect(studio.keys).toBe(0);
    expect(studio.keyHash).toBeNull();
    expect(studio.gates).toEqual(["needs-key"]);
    await expect
      .poll(() => page.evaluate(() => window.__paint.at(-1)?.state))
      .toBe("needs-key");
    const painted = await paintedSince(page, leftAt);
    const states = painted.map((s) => s.state);
    expect(states).not.toContain("connected");
    expect(states).toContain("connecting");
    expect(states[states.length - 1]).toBe("needs-key");
    console.log(`[paint] ${JSON.stringify(painted)}`);

    // Only explicitly opening a fresh link makes another one-shot handoff.
    const freshToken = newToken();
    await page.goto(
      link(PARENT, `${CHILD}/studio-like`, freshToken, "/dashboard/studio/runs/2"),
    );
    await expect(surface(page)).toHaveAttribute("data-state", "connected");
    const fresh = await waitForFrame(page, `${CHILD}/studio-like/child.html`);
    expect(await fresh.evaluate(() => window.__studio.keys)).toBe(1);
    expect(await fresh.evaluate(() => window.__studio.keyHash)).toBe(
      sha256(freshToken),
    );
    for (const target of [page, fresh]) {
      const storage = await target.evaluate(() =>
        JSON.stringify({
          local: { ...localStorage },
          session: { ...sessionStorage },
        }),
      );
      expect(storage).not.toContain(token);
      expect(storage).not.toContain(freshToken);
    }
  });

  test("reloading only the child loses the key and cannot trigger a host replay", async ({
    page,
    context,
  }) => {
    const token = newToken();
    await attachSink(context, { mainPage: () => page });
    await page.addInitScript(samplePaintedStates);
    await page.goto(link(PARENT, `${CHILD}/studio-like`, token));
    await expect(surface(page)).toHaveAttribute("data-state", "connected");
    const again = await waitForFrame(page, `${CHILD}/studio-like/child.html`);
    const leftAt = await page.evaluate(() => performance.now());
    await again.evaluate(() => window.location.reload());

    await expect(gate(page)).toHaveAttribute("data-state", "needs-key", {
      timeout: 15_000,
    });
    await expect(
      page.getByText("Local Studio needs to be reconnected"),
    ).toBeVisible();
    const studio = await again.evaluate(() => window.__studio);
    expect(studio.keys).toBe(0);
    expect(studio.keyHash).toBeNull();
    expect(studio.gates).toEqual(["needs-key"]);

    // The panel came from the child's gate (after its wait for a key), not
    // from the parent guessing on the first hello.
    await expect
      .poll(() => page.evaluate(() => window.__paint.at(-1)?.state))
      .toBe("needs-key");
    const painted = await paintedSince(page, leftAt);
    const needsKey = painted.find((s) => s.state === "needs-key");
    expect(needsKey.at - leftAt).toBeGreaterThan(KEY_WAIT_MS - 500);
    expect(KEY_CHECK_MS).toBeLessThan(KEY_WAIT_MS);
  });
});

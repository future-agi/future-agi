/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// The Studio tab end to end, in Chromium, Firefox and WebKit: the real
// Future AGI build (serve-harness.cjs) and the real, built `omega dev` from
// the accepted studio-embed head (omega-dev.cjs). The only fake is the model:
// the fixture's `router` entry uses a deterministic function model.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test, expect } = require("playwright/test");

const {
  analyticsRecords,
  attachSink,
  recordContains,
} = require("../../embed/e2e/sink.cjs");
const { deadPort, killAllOmegaDev, startOmegaDev } = require("./omega-dev.cjs");
const {
  parentSurfaces,
  runRouterInFrame,
  studioFrame,
  tokenLeaks,
  waitForAnalytics,
} = require("./studio-checks.cjs");

const PORT = Number(process.env.STUDIO_E2E_PARENT_PORT || 3051);
const PARENT = `http://localhost:${PORT}`;
const SCREENS =
  process.env.STUDIO_SCREENS_DIR ||
  path.join(process.env.STUDIO_E2E_WORK || os.tmpdir(), "screens");
const EVIDENCE = path.join(
  process.env.STUDIO_E2E_WORK || os.tmpdir(),
  "evidence",
);

const status = (page) => page.getByTestId("studio-status");
const gate = (page) => page.getByTestId("omega-embed-gate");

test.describe.configure({ mode: "serial" });

let embedded; // omega dev --embed-origin <Future AGI>
let plain; // omega dev with no embed origin: frame-ancestors 'none'

test.beforeAll(async ({ browserName }) => {
  fs.mkdirSync(EVIDENCE, { recursive: true });
  embedded = await startOmegaDev({
    name: `embed-${browserName}`,
    embedOrigins: [PARENT],
  });
  plain = await startOmegaDev({ name: `plain-${browserName}` });
});

test.afterAll(async () => {
  await embedded?.stop();
  await plain?.stop();
  killAllOmegaDev();
});

test("omega dev prints the Future AGI link only for the origin it was given", () => {
  expect(embedded.futureAgiLink).toBe(
    `${PARENT}/dashboard/studio#connect=${embedded.url}&t=${embedded.token}`,
  );
  expect(plain.futureAgiLink).toBeNull();
});

test("stage 2: the printed link frames Studio, a run inside the frame is omega dev's run, and the key stays out of Future AGI", async ({
  page,
  context,
  browserName,
}) => {
  const records = await attachSink(context, { mainPage: () => page });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(embedded.futureAgiLink);

  await expect(status(page)).toHaveAttribute("data-state", "connected", {
    timeout: 60_000,
  });
  // The intake took the fragment out before anything else ran.
  await expect(page).toHaveURL(`${PARENT}/dashboard/studio`);
  await expect(page.getByTestId("studio-connected")).toHaveAttribute(
    "data-source",
    "link",
  );
  const frame = await studioFrame(page, embedded.url);
  expect(frame.url().startsWith(`${embedded.url}/?embed=1`)).toBe(true);
  expect(frame.url()).not.toContain(embedded.token);

  // A scripted run inside the frame.
  const runId = await runRouterInFrame(frame);
  expect(runId).not.toBe("");
  // The frame moved to the run's thread; Future AGI's address follows it,
  // without reloading the frame.
  await expect(page).toHaveURL(
    new RegExp(`/dashboard/studio/t/[^?/]+\\?run=${runId}$`),
  );
  expect(frame.isDetached()).toBe(false);
  expect(await page.getByTestId("omega-embed-frame").count()).toBe(1);

  // omega dev's own runs list, asked directly with its token.
  const { runs } = await embedded.api("/studio/v1/runs");
  expect(runs.find((run) => run.id === runId)).toMatchObject({
    id: runId,
    status: "succeeded",
    entryId: "router",
  });

  // The frame's API calls carry the key, as a header only.
  const studioApi = records.filter((r) =>
    r.url.startsWith(`${embedded.url}/studio/v1/`),
  );
  expect(studioApi.length).toBeGreaterThan(1);
  expect(
    studioApi.every(
      (r) => r.headers.authorization === `Bearer ${embedded.token}`,
    ),
  ).toBe(true);

  // Future AGI's page never calls Studio itself: only the frame does.
  const parentToStudio = records.filter(
    (r) => r.isMainFrame && r.url.startsWith(embedded.url),
  );
  expect(parentToStudio.map((r) => r.url)).toEqual([]);

  // Analytics ran and saw nothing of the link.
  await waitForAnalytics(records);
  const marker = `studio-sink-selfcheck-${Date.now()}`;
  await page.evaluate((value) => {
    fetch(`https://www.google-analytics.com/g/collect?en=${value}`, {
      method: "POST",
      mode: "no-cors",
    }).catch(() => {});
  }, marker);
  await expect
    .poll(() => records.some((r) => recordContains(r, marker)))
    .toBe(true);
  expect(tokenLeaks(records, embedded.token, embedded.url)).toEqual([]);
  expect(
    analyticsRecords(records)
      .filter(
        (r) =>
          recordContains(r, "#connect") ||
          recordContains(r, "%23connect") ||
          recordContains(r, `127.0.0.1:${embedded.port}`),
      )
      .map((r) => r.url),
  ).toEqual([]);

  // URL, history, storage, cookies, window.name: no key anywhere.
  const surfaces = await parentSurfaces(page);
  for (const [where, value] of Object.entries(surfaces)) {
    expect(String(value), `token in ${where}`).not.toContain(embedded.token);
  }
  // The saved connection is an address and a label.
  expect(
    JSON.parse(
      await page.evaluate(() =>
        window.localStorage.getItem("omega.studio.connections"),
      ),
    ),
  ).toEqual([{ url: embedded.url, label: `127.0.0.1:${embedded.port}` }]);

  await page.screenshot({
    path: path.join(SCREENS, "engines", `connected-run-${browserName}.png`),
  });
  fs.writeFileSync(
    path.join(EVIDENCE, `stage2-${browserName}.json`),
    JSON.stringify(
      {
        browser: browserName,
        studio: embedded.url,
        runId,
        parentUrl: page.url(),
        frameUrl: frame.url(),
        omegaDevRun: runs.find((run) => run.id === runId),
        studioApiCalls: studioApi.length,
        requestsRecorded: records.length,
        analyticsRequests: analyticsRecords(records).length,
        tokenLeaks: 0,
        parentToStudio: 0,
      },
      null,
      2,
    ),
  );

  // A06: only the live child holds the key. Neither origin stores it.
  const childStorage = await frame.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(childStorage).not.toContain(embedded.token);
  await frame.evaluate(() => window.location.reload());
  await expect(gate(page)).toHaveAttribute("data-state", "needs-key", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
    "data-kind",
    "needs-key",
  );
  // Explicitly reopening the genuine printed link supplies a fresh handoff.
  await page.goto(embedded.futureAgiLink);
  await expect(status(page)).toHaveAttribute("data-state", "connected", {
    timeout: 60_000,
  });

  // A host reload restores only the URL, never the key.
  await page.reload();
  await expect(page.getByTestId("studio-connected")).toHaveAttribute(
    "data-source",
    "restored",
  );
  await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
    "data-kind",
    "needs-key",
    { timeout: 30_000 },
  );
  await expect(page).toHaveURL(`${PARENT}/dashboard/studio`);
  await page.getByTestId("studio-saved-connect").click();
  await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
    "data-kind",
    "needs-key",
    { timeout: 30_000 },
  );

  // The URL bookmark is still useful, but only a fresh paste supplies a key.
  await page.getByTestId("studio-connect-input").fill(embedded.localLink);
  await page.getByTestId("studio-connect-submit").click();
  await expect(status(page)).toHaveAttribute("data-state", "connected", {
    timeout: 60_000,
  });
  const reconnected = await studioFrame(page, embedded.url);
  for (const target of [page, reconnected]) {
    const storage = await target.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    );
    expect(storage).not.toContain(embedded.token);
  }
  expect(tokenLeaks(records, embedded.token, embedded.url)).toEqual([]);
});

test("pasting the local link omega dev printed connects too, and keeps the key out of storage", async ({
  page,
}) => {
  await page.goto(`${PARENT}/dashboard/studio`);
  await expect(page.getByTestId("studio-connect")).toBeVisible();
  await expect(page.getByTestId("studio-start-command-text")).toHaveText(
    `omega dev --no-open --embed-origin ${PARENT}`,
  );
  await page.getByTestId("studio-connect-input").fill(embedded.localLink);
  await page.getByTestId("studio-connect-submit").click();
  await expect(status(page)).toHaveAttribute("data-state", "connected", {
    timeout: 60_000,
  });
  const frame = await studioFrame(page, embedded.url);
  await frame.waitForSelector('[data-testid="entries-home"]', {
    timeout: 60_000,
  });
  const surfaces = await parentSurfaces(page);
  for (const [where, value] of Object.entries(surfaces)) {
    expect(String(value), `token in ${where}`).not.toContain(embedded.token);
  }
});

test("stage 1: a running omega dev that was not started for Future AGI is named within 6 s, and local Studio opens directly", async ({
  page,
  context,
}) => {
  // The real refusal: omega dev without --embed-origin forbids framing.
  const head = await fetch(`${plain.url}/?embed=1`);
  expect(head.headers.get("content-security-policy")).toContain(
    "frame-ancestors 'none'",
  );

  await page.goto(
    `${PARENT}/dashboard/studio#connect=${plain.url}&t=${plain.token}`,
  );
  await expect(page.getByTestId("omega-embed-frame")).toBeAttached();
  const mounted = Date.now();
  await expect(gate(page)).toHaveAttribute("data-cause", "framing-blocked", {
    timeout: 20_000,
  });
  const shownAfter = Date.now() - mounted;
  console.log(`[studio] framing-blocked shown ${shownAfter} ms after mount`);
  // Studio's hello bound (STUDIO_HELLO_TIMEOUT_MS), then one probe of its
  // server: the cause is on screen within 6 s of the frame mounting.
  expect(shownAfter).toBeLessThan(6000);
  await expect(gate(page)).toContainText(
    "Studio didn't open inside Future AGI",
  );
  await expect(page.getByTestId("studio-gate-fix-text")).toHaveText(
    `omega dev --no-open --embed-origin ${PARENT}`,
  );
  await expect(status(page)).toHaveAttribute("data-state", "timeout");

  const [popup] = await Promise.all([
    context.waitForEvent("page"),
    page.getByRole("button", { name: "Open local Studio directly" }).click(),
  ]);
  await popup.waitForLoadState("domcontentloaded");
  expect(popup.url()).toBe(`${plain.url}/`);
  await popup.close();
  fs.writeFileSync(
    path.join(EVIDENCE, `stage1-${test.info().project.name}.json`),
    JSON.stringify({ studio: plain.url, shownAfterMs: shownAfter }, null, 2),
  );
});

test("a bare address in a fresh tab: Studio answers that it has no key, and the tab asks for the link again", async ({
  page,
}) => {
  // Every newly mounted child is keyless; a bare address carries no key.
  await page.goto(`${PARENT}/dashboard/studio`);
  await page
    .getByTestId("studio-connect-input")
    .fill(embedded.url.replace("http://", ""));
  const started = Date.now();
  await page.getByTestId("studio-connect-submit").click();
  await expect(gate(page)).toContainText("Connecting to local Studio");
  await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
    "data-kind",
    "needs-key",
    { timeout: 30_000 },
  );
  const back = Date.now() - started;
  console.log(`[studio] back on the connect screen ${back} ms after Connect`);
  // Studio's own needs-key (after its 3 s wait for a key) closed the frame,
  // not EmbeddedSurface's 8 s fallback for a child that never answers
  // (8 s + the 1.5 s grace would be 9.5 s at the earliest).
  expect(back).toBeLessThan(9500);
  await expect(page.getByTestId("studio-connect-notice")).toContainText(
    "needs its key",
  );
  expect(await page.getByTestId("omega-embed-frame").count()).toBe(0);
});

test("says nothing is answering when omega dev is not running", async ({
  page,
}) => {
  const port = await deadPort();
  const server = `http://127.0.0.1:${port}`;
  await page.goto(`${PARENT}/dashboard/studio#connect=${server}&t=dead`);
  await expect(gate(page)).toHaveAttribute("data-cause", "not-running", {
    timeout: 20_000,
  });
  await expect(gate(page)).toContainText(`Can't reach Studio at ${server}`);
});

test("refuses a link whose server is not on this computer", async ({
  page,
}) => {
  await page.goto(
    `${PARENT}/dashboard/studio#connect=https://studio.example.com&t=abc`,
  );
  await expect(page.getByTestId("studio-connect-notice")).toHaveAttribute(
    "data-kind",
    "not-local",
  );
  expect(await page.getByTestId("omega-embed-frame").count()).toBe(0);
  await expect(page).toHaveURL(`${PARENT}/dashboard/studio`);
});

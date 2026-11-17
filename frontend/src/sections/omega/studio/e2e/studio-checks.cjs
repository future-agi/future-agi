/* eslint-env node */
// Helpers for the Studio tab e2e: driving the framed Studio (its own
// data-testid hooks, as Omega's studio-ui page objects use them), and the
// privacy checks on what Future AGI's page holds and sends.
const { expect } = require("playwright/test");

const { analyticsRecords, bodyVariants } = require("../../embed/e2e/sink.cjs");

const S = {
  entriesHome: '[data-testid="entries-home"]',
  entryRow: (id) => `[data-testid="entry-row"][data-entry-id="${id}"]`,
  flowNode: ".react-flow__node",
  inputEditor: '[data-testid="input-editor"]',
  inputMode: '[data-testid="input-mode"]',
  runInput: '[data-testid="run-input"]',
  runSubmit: '[data-testid="run-submit"]',
  runStatus: '[data-testid="run-status"]',
};

async function studioFrame(page, studioUrl, timeout = 30_000) {
  const find = () =>
    page.frames().find((frame) => frame.url().startsWith(`${studioUrl}/`));
  await expect.poll(() => Boolean(find()), { timeout }).toBe(true);
  return find();
}

// Opens the fixture's `router` entry inside the frame, runs it with a
// question and waits for it to succeed. Returns the run id the frame shows.
async function runRouterInFrame(frame, question = "Which lane?") {
  await frame.waitForSelector(S.entriesHome, { timeout: 60_000 });
  await frame.locator(S.entryRow("router")).click();
  await frame.waitForSelector(S.flowNode, { timeout: 30_000 });
  const editor = frame.locator(S.inputEditor);
  await editor.waitFor();
  if ((await editor.getAttribute("data-mode")) !== "raw") {
    await frame.locator(S.inputMode).click();
  }
  await frame.locator(S.runInput).fill(JSON.stringify({ question }));
  await frame.locator(S.runSubmit).click();
  await frame.waitForFunction(
    () =>
      document
        .querySelector('[data-testid="run-status"]')
        ?.getAttribute("data-status") === "succeeded",
    null,
    { timeout: 60_000 },
  );
  const hash = new URL(frame.url()).hash;
  return new URLSearchParams(hash.split("?")[1] || "").get("run") || "";
}

// Everything the Future AGI page keeps where a token could sit.
const parentSurfaces = (page) =>
  page.evaluate(() => ({
    href: window.location.href,
    history: JSON.stringify(window.history.state),
    local: JSON.stringify({ ...window.localStorage }),
    session: JSON.stringify({ ...window.sessionStorage }),
    cookie: document.cookie,
    name: window.name,
    intakeGlobal: String(window.__omegaConnect ?? null),
  }));

const urlVariants = (url) => {
  const out = [url];
  try {
    out.push(decodeURIComponent(url));
  } catch {
    /* keep raw */
  }
  return out;
};

// A request may carry the token in exactly one place: the Authorization
// header of the framed Studio's own API calls to its own origin. Anything
// else (a URL, a body, another header, another host, the Future AGI page
// itself) is a leak.
function tokenLeaks(records, token, studioUrl) {
  return records
    .filter((record) => {
      const studioCall =
        !record.isMainFrame &&
        record.url.startsWith(`${studioUrl}/`) &&
        record.frameUrl.startsWith(`${studioUrl}/`);
      const headers = Object.entries(record.headers || {})
        .filter(([name]) => !(studioCall && name === "authorization"))
        .map(([, value]) => value);
      const texts = [
        ...urlVariants(record.url),
        ...headers,
        ...bodyVariants(record.body),
      ];
      return texts.some((text) => text.includes(token));
    })
    .map((record) => record.url);
}

// Waits until the analytics SDKs have actually sent something, so a clean
// result is not clean only because nothing was sent.
async function waitForAnalytics(records) {
  await expect
    .poll(
      () =>
        analyticsRecords(records).some((r) =>
          r.url.includes("google-analytics.com/g/collect"),
        ),
      { timeout: 30_000, message: "GTM should have sent a page view" },
    )
    .toBe(true);
}

module.exports = {
  S,
  parentSurfaces,
  runRouterInFrame,
  studioFrame,
  tokenLeaks,
  waitForAnalytics,
};

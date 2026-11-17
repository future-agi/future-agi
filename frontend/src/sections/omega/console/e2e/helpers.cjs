/* eslint-env node */
// Shared steps for the console-frame e2e specs.
const { expect } = require("playwright/test");

const FAKE = `http://localhost:${Number(process.env.CONSOLE_E2E_FAKE_PORT || 3061)}`;
const EE = `http://localhost:${Number(process.env.CONSOLE_E2E_EE_PORT || 3062)}`;
const HAS_EE = Boolean(process.env.CONSOLE_E2E_EE_DIST);
// The two-origin topology (xo-hosts.cjs): host, Console origin, foreign site.
const XO_PORT = Number(process.env.CONSOLE_E2E_XO_HOST_PORT || 43310);
const XO = {
  HOST: `http://127.0.0.1:${XO_PORT}`,
  CONSOLE: `http://127.0.0.1:${XO_PORT + 1}`,
  FOREIGN: `http://127.0.0.1:${XO_PORT + 2}`,
};
const HAS_XO = Boolean(
  process.env.CONSOLE_E2E_XO_ORIGIN_DIR && process.env.CONSOLE_E2E_EE_FIXTURES,
);

// Nothing leaves this computer: every non-loopback request is refused.
async function keepLocal(context) {
  await context.route(
    (url) => !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname),
    (route) => route.abort(),
  );
}

const control = (request, base) => ({
  reset: () => request.post(`${base}/__e2e/reset`),
  mode: (body) => request.post(`${base}/__e2e/mode`, { data: body }),
  state: async () => (await request.get(`${base}/__e2e/state`)).json(),
});

const framePage = (page) => page.getByTestId("omega-tab-console");

async function expectConnected(page) {
  await expect(framePage(page)).toHaveAttribute("data-state", "connected");
  await expect(page.getByTestId("omega-embed-gate")).toHaveCount(0);
}

// The framed Console's document (same site, under /omega/console/).
function consoleFrame(page) {
  return page.frames().find((frame) => frame.url().includes("/omega/console/"));
}

async function waitForConsoleFrame(page) {
  await expect.poll(() => Boolean(consoleFrame(page))).toBe(true);
  return consoleFrame(page);
}

const pathOf = (page) => {
  const url = new URL(page.url());
  return `${url.pathname}${url.search}`;
};

// A mint failure is known as soon as the exchange answers, so its reason must
// be on screen well before the EE Console's own 6 s wait for a key (after
// which it reports needs-key itself) and the shared surface's 8 s fallback.
const SHOWN_AT_ONCE_MS = 3_000;

// Notes, in the Future AGI page, when the framed Console first said hello.
async function recordFirstHello(page) {
  await page.addInitScript(() => {
    if (window.top !== window) return;
    window.addEventListener("message", (event) => {
      if (event.data?.type === "embed.hello" && !window.__firstHelloAt) {
        window.__firstHelloAt = Date.now();
      }
    });
  });
}

// Milliseconds from the Console's first hello until the gate shows `state`.
async function msFromFirstHelloTo(page, state) {
  const handle = await page.waitForFunction(
    (wanted) => {
      const gate = document.querySelector('[data-testid="omega-embed-gate"]');
      if (gate?.dataset.state !== wanted || !window.__firstHelloAt) return 0;
      return Math.max(1, Date.now() - window.__firstHelloAt);
    },
    state,
    { polling: 50, timeout: 20_000 },
  );
  return handle.jsonValue();
}

async function setHostTheme(page, mode) {
  await page.waitForFunction(() => Boolean(window.__consoleE2E));
  await page.evaluate((next) => window.__consoleE2E.setThemeMode(next), mode);
}

// Every value the parent page could have leaked a key into.
async function parentSurfaces(page) {
  return page.evaluate(() => {
    const dump = (storage) => {
      const out = [];
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        out.push(`${key}=${storage.getItem(key)}`);
      }
      return out.join("\n");
    };
    const frame = document.querySelector('[data-testid="omega-embed-frame"]');
    return [
      window.location.href,
      frame ? frame.getAttribute("src") : "",
      frame?.contentWindow?.location.href || "",
      dump(window.localStorage),
      dump(window.sessionStorage),
      document.cookie,
      document.documentElement.outerHTML,
    ].join("\n");
  });
}

module.exports = {
  EE,
  FAKE,
  HAS_EE,
  HAS_XO,
  XO,
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
};

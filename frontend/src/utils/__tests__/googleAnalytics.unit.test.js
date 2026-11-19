import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { JSDOM } from "jsdom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MEASUREMENT_ID = "G-TEST123";
const config = vi.hoisted(() => ({ GA_ID: "G-TEST123" }));
vi.mock("src/config-global", () => config);

const originalPushState = window.history.pushState;
const originalReplaceState = window.history.replaceState;

// The real address check from index.html (paths and query/hash keys), run in
// its own document so its non-configurable globals don't leak between tests.
const indexHtml = new JSDOM(
  readFileSync(resolve(cwd(), "index.html"), "utf8"),
  { runScripts: "dangerously", url: "https://app.test/" },
);
const indexHtmlPolicy = indexHtml.window.__analyticsUrlAllowed;

function installPolicy({ allowedAtLoad = true } = {}) {
  window.__analyticsUrlAllowed = (value) =>
    indexHtmlPolicy(new URL(value, document.baseURI).href);
  window.__analyticsAllowedAtLoad = allowedAtLoad;
}

function gtagScripts() {
  return [...document.querySelectorAll("script[src]")]
    .map((script) => script.getAttribute("src"))
    .filter((src) => src.includes("googletagmanager.com/gtag/js"));
}

// Small router supplied by the test, not a second inventory of app routes.
const routes = [
  {
    path: "/",
    children: [
      { index: true },
      {
        path: "dashboard",
        children: [
          { index: true },
          { path: "develop" },
          { path: "users/:label" },
          {
            path: "folders/:folder",
            children: [
              { path: "reports/:report", children: [{ index: true }] },
            ],
          },
          { path: "files/*" },
          { path: "boards/:board?" },
          { path: "/dashboard/summary" },
        ],
      },
      { children: [{ path: "settings/billing" }] },
      { path: "*" },
    ],
  },
];

async function loadAndInit(tree = routes) {
  const { initGoogleAnalytics } = await import("../googleAnalytics");
  return initGoogleAnalytics(tree);
}

// Each test initializes the loader again; remove the window listeners it adds
// so an earlier test's listeners don't react to a later test's navigation.
const windowListeners = [];
const addWindowListener = window.addEventListener.bind(window);

beforeEach(() => {
  vi.resetModules();
  config.GA_ID = MEASUREMENT_ID;
  window.history.replaceState(null, "", "/dashboard/develop");
  vi.spyOn(window, "addEventListener").mockImplementation((...args) => {
    windowListeners.push(args);
    addWindowListener(...args);
  });
});

afterEach(() => {
  windowListeners
    .splice(0)
    .forEach((args) => window.removeEventListener(...args));
  vi.restoreAllMocks();
  window.history.pushState = originalPushState;
  window.history.replaceState = originalReplaceState;
  document.head
    .querySelectorAll("script[src]")
    .forEach((script) => script.remove());
  delete window.__analyticsUrlAllowed;
  delete window.__analyticsAllowedAtLoad;
  delete window.dataLayer;
  delete window.gtag;
  delete window[`ga-disable-${MEASUREMENT_ID}`];
});

describe("index.html address check unit", () => {
  it.each([
    "https://app.test/auth/jwt/verify/u/t",
    "https://app.test/shared/t",
    "https://app.test/%73hared/t",
    "https://app.test//shared/t",
    "https://app.test/auth/jwt/invitation/set-password/u/t",
    "https://app.test/SOS",
    "https://app.test/dashboard?sso_token=x",
    "https://app.test/dashboard?refresh_token=x",
    "https://app.test/dashboard?code=x",
    "https://app.test/dashboard#access_token=x",
    "https://app.test/%E0%A4%A",
  ])("blocks %s", (url) => {
    expect(indexHtmlPolicy(url)).toBe(false);
  });

  it.each([
    "https://app.test/dashboard/develop?tab=runs",
    "https://app.test/auth/jwt/login?returnTo=%2Fdashboard",
  ])("allows %s", (url) => {
    expect(indexHtmlPolicy(url)).toBe(true);
  });
});

describe("analyticsPageLocation unit", () => {
  it.each([
    ["/", "/"],
    ["/dashboard", "/dashboard"],
    ["/DaShBoArD/Develop?tab=blue#section", "/dashboard/develop"],
    [
      "/dashboard?utm_source=x&utm_campaign=y",
      "/dashboard?utm_source=x&utm_campaign=y",
    ],
    ["/dashboard/users/summary", "/dashboard/users/:id"],
    ["/dashboard/users/%73ummary", "/dashboard/users/:id"],
    [
      "/dashboard/folders/summary/reports/billing",
      "/dashboard/folders/:id/reports/:id",
    ],
    ["/dashboard/files/summary/settings/billing", "/dashboard/files/:id"],
    ["/dashboard/boards", "/dashboard/boards"],
    ["/dashboard/boards/develop", "/dashboard/boards/:id"],
    ["/dashboard/summary", "/dashboard/summary"],
    ["/%64ashboard", "/dashboard"],
    ["/settings/billing", "/settings/billing"],
    ["/develop/summary/billing", "/:id"],
  ])("maps %s to its matched template %s", async (input, expected) => {
    const { analyticsPageLocation } = await import("../googleAnalytics");
    expect(analyticsPageLocation(`https://app.test${input}`, routes)).toBe(
      `https://app.test${expected}`,
    );
  });

  it("redacts the entire path when no route matches", async () => {
    const { analyticsPageLocation } = await import("../googleAnalytics");
    expect(analyticsPageLocation("https://app.test/summary/billing", [])).toBe(
      "https://app.test/:id",
    );
  });
});

describe("analyticsInitialReferrer unit", () => {
  it.each([
    ["", ""],
    ["https://search.test/results?q=topic", "https://search.test/"],
    ["https://info.test/plans?utm_source=x", "https://info.test/"],
    ["https://app.test/shared/abc?sso_token=x", ""],
    ["https://app.test/dashboard", ""],
    ["android-app://com.example", ""],
    ["not a url", ""],
  ])("maps %j to %j", async (input, expected) => {
    const { analyticsInitialReferrer } = await import("../googleAnalytics");
    expect(analyticsInitialReferrer(input, "https://app.test")).toBe(expected);
  });
});

describe("initGoogleAnalytics unit", () => {
  it("loads gtag.js and configures GA4 when the build sets an ID", async () => {
    installPolicy();
    await loadAndInit();
    expect(gtagScripts()).toEqual([
      `https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`,
    ]);
    const configCall = window.dataLayer.find((args) => args[0] === "config");
    expect(configCall[1]).toBe(MEASUREMENT_ID);
    expect(window[`ga-disable-${MEASUREMENT_ID}`]).toBe(false);
  });

  it.each([
    "",
    undefined,
    "GTM-ABC",
    "AW-123",
    "G-abc",
    "G-1 2",
    "%VITE_GA_ID%",
  ])("loads nothing for the build value %j", async (value) => {
    config.GA_ID = value;
    installPolicy();
    await loadAndInit();
    expect(gtagScripts()).toEqual([]);
    expect(window.dataLayer).toBeUndefined();
  });

  it("loads nothing when the page address was blocked at load", async () => {
    installPolicy({ allowedAtLoad: false });
    await loadAndInit();
    expect(gtagScripts()).toEqual([]);
  });

  it.each([
    ["missing policy", () => delete window.__analyticsUrlAllowed],
    ["missing load veto", () => delete window.__analyticsAllowedAtLoad],
    [
      "non-function policy",
      () => {
        window.__analyticsUrlAllowed = true;
      },
    ],
    // Navigation during boot, after a positive index.html snapshot.
    [
      "blocked current registration URL",
      () =>
        window.history.replaceState(
          null,
          "",
          "/auth/jwt/register?onboarding_token=blue",
        ),
    ],
  ])("does not install the tag, queue or hooks on %s", async (_, change) => {
    installPolicy();
    change();
    await loadAndInit();
    expect(gtagScripts()).toEqual([]);
    expect(window.dataLayer).toBeUndefined();
    expect(window.gtag).toBeUndefined();
    expect(window[`ga-disable-${MEASUREMENT_ID}`]).toBeUndefined();
    expect(window.history.pushState).toBe(originalPushState);
    expect(window.history.replaceState).toBe(originalReplaceState);
    const types = windowListeners.map(([type]) => type);
    expect(types).not.toContain("popstate");
    expect(types).not.toContain("hashchange");
  });

  it("disables hits on in-app navigation to a one-time link and re-enables after", async () => {
    installPolicy();
    await loadAndInit();
    const flag = `ga-disable-${MEASUREMENT_ID}`;

    window.history.pushState(null, "", "/auth/jwt/verify/uuid-1/token-1");
    expect(window[flag]).toBe(true);

    window.history.replaceState(null, "", "/dashboard/develop");
    expect(window[flag]).toBe(false);

    window.history.pushState(null, "", "/shared/token-1");
    expect(window[flag]).toBe(true);
    window.history.replaceState(null, "", "/dashboard/develop");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(window[flag]).toBe(false);
  });

  it("configures a redacted page location without reading the page title", async () => {
    const readTitle = vi
      .spyOn(document, "title", "get")
      .mockReturnValue("Blue board");
    window.history.replaceState(
      null,
      "",
      "/dashboard/folders/summary/reports/billing?search=blue&utm_source=newsletter&gclid=abc#frag",
    );
    installPolicy();
    await loadAndInit();
    const configCall = window.dataLayer.find((args) => args[0] === "config");
    expect(readTitle).not.toHaveBeenCalled();
    expect(configCall[2].page_title).toBe("Future AGI");
    expect(configCall[2].page_location).toBe(
      `${window.location.origin}/dashboard/folders/:id/reports/:id?utm_source=newsletter&gclid=abc`,
    );
  });

  const pageViews = () =>
    window.dataLayer
      .filter((args) => args[0] === "event" && args[1] === "page_view")
      .map((args) => args[2]);

  it.each(["config", "event"])(
    "rechecks the current URL if the existing queue navigates during the first %s",
    async (commandName) => {
      installPolicy();
      const layer = [];
      window.dataLayer = layer;
      let navigated = false;
      vi.spyOn(layer, "push").mockImplementation((command) => {
        const length = Array.prototype.push.call(layer, command);
        if (!navigated && command[0] === commandName) {
          navigated = true;
          window.history.pushState(null, "", "/shared/blue");
        }
        return length;
      });
      await loadAndInit();
      expect(gtagScripts()).toHaveLength(0);
      expect(window[`ga-disable-${MEASUREMENT_ID}`]).toBe(true);
      expect(pageViews()).toHaveLength(commandName === "event" ? 1 : 0);
      window.history.replaceState(null, "", "/settings/billing");
      expect(gtagScripts()).toHaveLength(1);
      expect(pageViews().at(-1).page_referrer).toBe(
        commandName === "event"
          ? `${window.location.origin}/dashboard/develop`
          : "",
      );
    },
  );

  it("sends the first page view itself, not through gtag's automatic one", async () => {
    installPolicy();
    await loadAndInit();
    const configCall = window.dataLayer.find((args) => args[0] === "config");
    expect(configCall[2].send_page_view).toBe(false);
    expect(configCall[2].allow_google_signals).toBe(false);
    expect(configCall[2].allow_ad_personalization_signals).toBe(false);
    expect(pageViews()).toEqual([
      {
        page_location: `${window.location.origin}/dashboard/develop`,
        page_referrer: "",
        page_title: "Future AGI",
      },
    ]);
  });

  it("sends a redacted page view for each in-app navigation", async () => {
    installPolicy();
    await loadAndInit();
    const origin = window.location.origin;
    window.history.pushState(null, "", "/dashboard/users/summary?section=blue");
    expect(pageViews().at(-1)).toEqual({
      page_location: `${origin}/dashboard/users/:id`,
      page_referrer: `${origin}/dashboard/develop`,
      page_title: "Future AGI",
    });
    const updates = window.dataLayer.filter(
      (args) => args[0] === "config" && args[2]?.update === true,
    );
    // Every configuration call repeats the privacy settings.
    expect(updates.at(-1)[2]).toEqual({
      page_location: `${origin}/dashboard/users/:id`,
      page_referrer: `${origin}/dashboard/develop`,
      page_title: "Future AGI",
      send_page_view: false,
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
      linker: {
        domains: ["futureagi.com", "app.futureagi.com"],
        accept_incoming: true,
      },
      update: true,
    });
  });

  it("blocks in-app navigation using the real query-key list", async () => {
    installPolicy();
    await loadAndInit();
    const flag = `ga-disable-${MEASUREMENT_ID}`;
    const before = window.dataLayer.length;
    window.history.pushState(null, "", "/dashboard?oauth_code=x");
    expect(window[flag]).toBe(true);
    window.history.pushState(null, "", "/%73hared/token-1");
    expect(window[flag]).toBe(true);
    expect(window.dataLayer.length).toBe(before);
  });

  it("counts coming back from a blocked page as a page view", async () => {
    installPolicy();
    await loadAndInit();
    const origin = window.location.origin;
    window.history.pushState(null, "", "/shared/token-1");
    window.history.replaceState(null, "", "/dashboard/develop");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(pageViews()).toHaveLength(2);
    expect(pageViews().at(-1)).toEqual({
      page_location: `${origin}/dashboard/develop`,
      page_referrer: `${origin}/dashboard/develop`,
      page_title: "Future AGI",
    });
  });

  it("counts distinct paths with the same template, but never reports either label", async () => {
    window.history.replaceState(null, "", "/dashboard/users/summary");
    installPolicy();
    await loadAndInit();
    window.history.pushState(null, "", "/dashboard/users/billing");
    // Simulate Back: the browser changes the address without calling pushState.
    originalReplaceState.call(
      window.history,
      null,
      "",
      "/dashboard/users/summary",
    );
    window.dispatchEvent(new PopStateEvent("popstate"));
    const label = `${window.location.origin}/dashboard/users/:id`;
    const locations = pageViews().map((view) => [
      view.page_location,
      view.page_referrer,
    ]);
    expect(locations).toEqual([
      [label, ""],
      [label, label],
      [label, label],
    ]);
    for (const command of window.dataLayer) {
      expect(JSON.stringify(command)).not.toMatch(/summary|billing/);
    }
  });

  it("does not count query, campaign or fragment edits as page views", async () => {
    window.history.replaceState(
      null,
      "",
      "/dashboard/develop?utm_source=first",
    );
    installPolicy();
    await loadAndInit();
    window.history.replaceState(null, "", "/dashboard/develop?tab=runs");
    window.history.pushState(null, "", "/dashboard/develop?utm_source=second");
    window.history.replaceState(null, "", "/dashboard/develop#panel");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(pageViews()).toHaveLength(1);
    window.history.pushState(null, "", "/settings/billing");
    expect(pageViews().at(-1).page_referrer).toBe(
      `${window.location.origin}/dashboard/develop?utm_source=first`,
    );
  });

  it("sends nothing for a blocked address and never uses it as a referrer", async () => {
    installPolicy();
    await loadAndInit();
    const origin = window.location.origin;
    const before = window.dataLayer.length;
    window.history.pushState(null, "", "/shared/token-1");
    expect(window.dataLayer.length).toBe(before);
    window.history.pushState(null, "", "/settings/billing");
    expect(pageViews().at(-1)).toEqual({
      page_location: `${origin}/settings/billing`,
      page_referrer: `${origin}/dashboard/develop`,
      page_title: "Future AGI",
    });
  });

  it("waits for a route tree, then reports the current address as the first view", async () => {
    installPolicy();
    await loadAndInit([]);
    expect(gtagScripts()).toHaveLength(0);
    expect(window.dataLayer).toBeUndefined();
    window.history.pushState(null, "", "/dashboard/users/billing");
    await loadAndInit();
    expect(pageViews()).toEqual([
      {
        page_location: `${window.location.origin}/dashboard/users/:id`,
        page_referrer: "",
        page_title: "Future AGI",
      },
    ]);
  });

  it("rebinds the current tree without reinstalling hooks or duplicating the first view", async () => {
    installPolicy();
    const unbind = await loadAndInit();
    const push = window.history.pushState;
    unbind(); // Strict-mode layout effect cleanup, then setup on the same URL.
    const unbindAgain = await loadAndInit();
    expect(gtagScripts()).toHaveLength(1);
    expect(window.history.pushState).toBe(push);
    expect(pageViews()).toHaveLength(1);

    unbindAgain(); // A suspended/unmounted router no longer supplies templates.
    expect(window[`ga-disable-${MEASUREMENT_ID}`]).toBe(true);
    window.history.pushState(null, "", "/dashboard/summary");
    expect(pageViews()).toHaveLength(1);
    await loadAndInit([{ path: "/dashboard/:item" }]);
    expect(pageViews().at(-1)).toEqual({
      page_location: `${window.location.origin}/dashboard/:id`,
      page_referrer: `${window.location.origin}/dashboard/develop`,
      page_title: "Future AGI",
    });
  });
});

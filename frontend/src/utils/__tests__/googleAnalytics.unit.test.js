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

function installPolicy({ allowedAtLoad = true, withCheck = true } = {}) {
  if (withCheck) {
    window.__analyticsUrlAllowed = (value) =>
      indexHtmlPolicy(new URL(value, document.baseURI).href);
  }
  window.__analyticsAllowedAtLoad = allowedAtLoad;
}

function gtagScripts() {
  return [...document.querySelectorAll("script[src]")]
    .map((script) => script.getAttribute("src"))
    .filter((src) => src.includes("googletagmanager.com/gtag/js"));
}

async function loadAndInit() {
  const { initGoogleAnalytics } = await import("../googleAnalytics");
  initGoogleAnalytics();
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
    ["https://app.test/dashboard", "https://app.test/dashboard"],
    [
      "https://app.test/dashboard/develop?tab=x&q=secret#h",
      "https://app.test/dashboard/develop",
    ],
    [
      "https://app.test/dashboard?utm_source=x&utm_campaign=y",
      "https://app.test/dashboard?utm_source=x&utm_campaign=y",
    ],
    [
      "https://app.test/dashboard/develop/3F2C1E4B-9A7D-4C1E-8F00-1234567890AB/runs/42",
      "https://app.test/dashboard/develop/:id/runs/:id",
    ],
    // End-user IDs and folder names are not route words, so they are redacted.
    [
      "https://app.test/dashboard/users/alice",
      "https://app.test/dashboard/users/:id",
    ],
    [
      "https://app.test/dashboard/workbench/My%20Folder",
      "https://app.test/dashboard/workbench/:id",
    ],
    [
      "https://app.test/dashboard/users/someone%40example.com",
      "https://app.test/dashboard/users/:id",
    ],
    ["https://app.test/dashboard/%E0%A4%A", "https://app.test/dashboard/:id"],
    [
      "https://app.test//dashboard//develop",
      "https://app.test/dashboard/develop",
    ],
    ["https://app.test/%64ashboard", "https://app.test/dashboard"],
    ["https://app.test/settings/billing", "https://app.test/settings/billing"],
  ])("maps %s to %s", async (input, expected) => {
    const { analyticsPageLocation } = await import("../googleAnalytics");
    expect(analyticsPageLocation(input)).toBe(expected);
  });
});

describe("analyticsInitialReferrer unit", () => {
  it.each([
    ["", ""],
    ["https://www.google.com/search?q=secret", "https://www.google.com/"],
    ["https://futureagi.com/pricing?utm_source=x", "https://futureagi.com/"],
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

  it("fails closed when index.html's address check is missing", async () => {
    installPolicy({ withCheck: false });
    await loadAndInit();
    expect(gtagScripts()).toEqual([]);
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

  it("configures a redacted page location and a fixed title", async () => {
    window.history.replaceState(
      null,
      "",
      "/dashboard/develop/3f2c1e4b-9a7d-4c1e-8f00-1234567890ab/runs/123456?search=secret&utm_source=newsletter&gclid=abc#frag",
    );
    installPolicy();
    await loadAndInit();
    const configCall = window.dataLayer.find((args) => args[0] === "config");
    expect(configCall[2].page_title).toBe("Future AGI");
    expect(configCall[2].page_location).toBe(
      `${window.location.origin}/dashboard/develop/:id/runs/:id?utm_source=newsletter&gclid=abc`,
    );
  });

  const pageViews = () =>
    window.dataLayer
      .filter((args) => args[0] === "event" && args[1] === "page_view")
      .map((args) => args[2]);

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
    window.history.pushState(null, "", "/auth/jwt/register?email=a%40b.com");
    expect(pageViews().at(-1)).toEqual({
      page_location: `${origin}/auth/jwt/register`,
      page_referrer: `${origin}/dashboard/develop`,
      page_title: "Future AGI",
    });
    const updates = window.dataLayer.filter(
      (args) => args[0] === "config" && args[2]?.update === true,
    );
    // Every configuration call repeats the privacy settings.
    expect(updates.at(-1)[2]).toEqual({
      page_location: `${origin}/auth/jwt/register`,
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

  it("does not count query-only changes as page views", async () => {
    installPolicy();
    await loadAndInit();
    window.history.replaceState(null, "", "/dashboard/develop?tab=runs");
    window.history.replaceState(null, "", "/dashboard/develop?search=x");
    expect(pageViews()).toHaveLength(1);
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

  it("initializes once", async () => {
    installPolicy();
    const { initGoogleAnalytics } = await import("../googleAnalytics");
    initGoogleAnalytics();
    initGoogleAnalytics();
    expect(gtagScripts()).toHaveLength(1);
  });
});

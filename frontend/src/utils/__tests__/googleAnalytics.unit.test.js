import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const MEASUREMENT_ID = "G-TEST123";
const config = vi.hoisted(() => ({ GA_ID: "G-TEST123" }));
vi.mock("src/config-global", () => config);

const originalPushState = window.history.pushState;
const originalReplaceState = window.history.replaceState;

// Mirrors index.html's address check closely enough to drive the loader.
const blocked = /^\/(?:auth\/jwt\/verify\/|shared\/|sos(?:\/|$))/i;
function installPolicy({ allowedAtLoad = true, withCheck = true } = {}) {
  if (withCheck) {
    window.__analyticsUrlAllowed = (value) =>
      !blocked.test(new URL(value, document.baseURI).pathname);
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

beforeEach(() => {
  vi.resetModules();
  config.GA_ID = MEASUREMENT_ID;
  window.history.replaceState(null, "", "/dashboard/develop");
});

afterEach(() => {
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

describe("analyticsPageLocation unit", () => {
  it.each([
    ["https://app.test/dashboard", "https://app.test/dashboard"],
    ["https://app.test/a?tab=x&q=secret#h", "https://app.test/a"],
    ["https://app.test/a?utm_source=x&utm_campaign=y", "https://app.test/a?utm_source=x&utm_campaign=y"],
    ["https://app.test/p/3F2C1E4B-9A7D-4C1E-8F00-1234567890AB", "https://app.test/p/:id"],
    ["https://app.test/p/0123456789abcdef0123", "https://app.test/p/:id"],
    ["https://app.test/p/12345", "https://app.test/p/:id"],
    ["https://app.test/p/123", "https://app.test/p/123"],
    ["https://app.test/p/eyJhbGciOiJIUzI1NiJ9abcdef", "https://app.test/p/:id"],
    ["https://app.test/u/someone%40example.com", "https://app.test/u/:id"],
    ["https://app.test/u/%E0%A4%A", "https://app.test/u/:id"],
    ["https://app.test/settings/billing", "https://app.test/settings/billing"],
  ])("maps %s to %s", async (input, expected) => {
    const { analyticsPageLocation } = await import("../googleAnalytics");
    expect(analyticsPageLocation(input)).toBe(expected);
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

  it.each(["", undefined, "GTM-ABC", "AW-123", "G-abc", "G-1 2", "%VITE_GA_ID%"])(
    "loads nothing for the build value %j",
    async (value) => {
      config.GA_ID = value;
      installPolicy();
      await loadAndInit();
      expect(gtagScripts()).toEqual([]);
      expect(window.dataLayer).toBeUndefined();
    },
  );

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

  it("refreshes the page fields after in-app navigation", async () => {
    installPolicy();
    await loadAndInit();
    window.history.pushState(null, "", "/auth/jwt/register?email=a%40b.com");
    const updates = window.dataLayer.filter(
      (args) => args[0] === "config" && args[2]?.update === true,
    );
    expect(updates.at(-1)[2]).toEqual({
      page_location: `${window.location.origin}/auth/jwt/register`,
      page_title: "Future AGI",
      send_page_view: false,
      update: true,
    });
  });

  it("sends no page field update for a blocked address", async () => {
    installPolicy();
    await loadAndInit();
    const before = window.dataLayer.length;
    window.history.pushState(null, "", "/shared/token-1");
    expect(window.dataLayer.length).toBe(before);
  });

  it("initializes once", async () => {
    installPolicy();
    const { initGoogleAnalytics } = await import("../googleAnalytics");
    initGoogleAnalytics();
    initGoogleAnalytics();
    expect(gtagScripts()).toHaveLength(1);
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

// The page every self-hosted user loads first, before any React code runs.
const indexHtmlSource = readFileSync(resolve(cwd(), "index.html"), "utf8");

// Vite's %ENV% substitution in index.html: a key the build defines is
// replaced, any other is left as is.
const build = (env) =>
  indexHtmlSource.replace(/%(\S+?)%/g, (text, key) =>
    key in env ? env[key] : text,
  );

const GOOGLE_HOST = /google\.com|gstatic\.com|googletagmanager\.com/;

const urlsIn = (document) =>
  [...document.querySelectorAll("[src], [href]")].map(
    (el) => el.getAttribute("src") || el.getAttribute("href"),
  );

// Every URL the page asks for once its inline scripts ran, plus what a
// browser without JavaScript fetches: parsed with scripting off, <noscript>
// content is markup.
function googleUrls(env, url) {
  const html = build(env);
  const withScripts = new JSDOM(html, {
    runScripts: "dangerously",
    ...(url && { url }),
  });
  const withoutScripts = new JSDOM(html);
  const urls = new Set([
    ...urlsIn(withScripts.window.document),
    ...urlsIn(withoutScripts.window.document),
  ]);
  return [...urls].filter((url) => GOOGLE_HOST.test(url));
}

describe("index.html third-party requests", () => {
  it("contacts no Google host when the build sets no key", () => {
    expect(googleUrls({})).toEqual([]);
  });

  it("contacts no Google host when the keys are set but empty", () => {
    expect(googleUrls({ VITE_GTM_ID: "", VITE_GOOGLE_SITE_KEY: "" })).toEqual(
      [],
    );
  });

  it("loads Tag Manager when the build sets VITE_GTM_ID", () => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" })).toEqual([
      "https://www.googletagmanager.com/gtm.js?id=GTM-TEST",
    ]);
  });

  it.each([
    "https://app.example.test/auth/jwt/verify/uuid-1/token-1",
    "https://app.example.test/AUTH/JWT/VERIFY/uuid-1/token-1",
    "https://app.example.test/auth/jwt/invitation/accept/uuid-1/token-1",
    "https://app.example.test/auth/jwt/invitation/set-password/uuid-1/token-1",
    "https://app.example.test/shared/token-1",
    "https://app.example.test/sos",
    "https://app.example.test/sos/",
    "https://app.example.test/dashboard?sso_token=a&refresh_token=b",
    "https://app.example.test/dashboard?access_token=a",
    "https://app.example.test/auth/jwt/login#access_token=a",
    "https://app.example.test/mcp/consent?code=a",
    "https://app.example.test/%73hared/token-1",
    "https://app.example.test//shared/token-1",
    "https://app.example.test/auth/jwt/%76erify/uuid-1/token-1",
  ])("does not load Tag Manager on %s", (url) => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" }, url)).toEqual([]);
  });

  it.each([
    "https://app.example.test/auth/jwt/login",
    "https://app.example.test/auth/jwt/verify",
    "https://app.example.test/dashboard/develop?utm_source=x",
    "https://app.example.test/sosx",
  ])("loads Tag Manager on %s", (url) => {
    expect(googleUrls({ VITE_GTM_ID: "GTM-TEST" }, url)).toEqual([
      "https://www.googletagmanager.com/gtm.js?id=GTM-TEST",
    ]);
  });

  it("leaves reCAPTCHA to the app when the build sets a site key", () => {
    expect(googleUrls({ VITE_GOOGLE_SITE_KEY: "site-key" })).toEqual([]);
  });
});

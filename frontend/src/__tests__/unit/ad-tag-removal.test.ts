import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(path, "utf8");

describe("unused tag and ad cleanup", () => {
  it("removes tag loading and conversion calls without changing callback startup", () => {
    expect(read("index.html")).not.toContain("googletagmanager.com");
    for (const path of [
      "src/main.jsx",
      "src/auth/guard/auth-guard.jsx",
      "src/sections/auth/jwt/jwt-register-view.jsx",
    ]) {
      expect(read(path)).not.toMatch(
        /(?:googleAds|redditAds|twitterAds|initGoogleAds|initReddit|initTwitter)/,
      );
    }
    expect(read("index.html")).toContain(
      'name="referrer" content="no-referrer"',
    );
    expect(read("index.html")).toContain('id="saml-initial-marker-guard"');
    expect(read("src/entry.js")).toContain("!window.__authHandoffScrubFailed");
  });
  it("retains the other independent analytics integrations", () => {
    expect(read("src/main.jsx")).toContain("initPostHog();");
    expect(read("src/main.jsx")).toContain("Sentry.init({");
  });
});

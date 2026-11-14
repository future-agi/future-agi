import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(path, "utf8");

describe("browser agent cleanup", () => {
  it("removes the agent from app startup and dependencies", () => {
    expect(read("src/app.jsx")).not.toMatch(/BrowserAgent|newRelicTracing/);
    expect(JSON.parse(read("package.json")).dependencies).not.toHaveProperty(
      "@newrelic/browser-agent",
    );
  });
  it("retains Sentry, PostHog and the existing callback entry", () => {
    expect(read("src/main.jsx")).toContain("Sentry.init({");
    expect(read("src/main.jsx")).toContain("initPostHog();");
    expect(read("src/entry.js")).toContain("!window.__authHandoffScrubFailed");
  });
});

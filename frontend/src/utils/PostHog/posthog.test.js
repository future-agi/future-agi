import { describe, expect, it } from "vitest";
import { isUsablePostHogKey } from "./posthog";

describe("PostHog key guard", () => {
  it("rejects empty and placeholder keys", () => {
    expect(isUsablePostHogKey()).toBe(false);
    expect(isUsablePostHogKey("")).toBe(false);
    expect(isUsablePostHogKey("your_posthog_project_api_key")).toBe(false);
    expect(isUsablePostHogKey("phc_your_project_api_key")).toBe(false);
    expect(isUsablePostHogKey("your-local-key")).toBe(false);
    expect(isUsablePostHogKey("phc_local_onboarding_smoke")).toBe(false);
  });

  it("accepts plausible project keys", () => {
    expect(isUsablePostHogKey("phc_live_project_key")).toBe(true);
  });

  it("allows local smoke keys only when browser proof opts in", () => {
    window.__FUTURE_AGI_ENABLE_POSTHOG_SMOKE__ = true;
    expect(isUsablePostHogKey("phc_local_onboarding_smoke")).toBe(true);
    delete window.__FUTURE_AGI_ENABLE_POSTHOG_SMOKE__;
  });
});

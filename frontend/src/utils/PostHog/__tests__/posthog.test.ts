import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  config: {
    POSTHOG_KEY: "test-key",
    POSTHOG_HOST: "",
    CURRENT_ENVIRONMENT: "production",
  },
  logger: { debug: vi.fn(), error: vi.fn() },
  sdk: {
    init: vi.fn(),
    capture: vi.fn(),
    identify: vi.fn(),
    group: vi.fn(),
    reset: vi.fn(),
    isFeatureEnabled: vi.fn(),
    getFeatureFlagPayload: vi.fn(),
    onFeatureFlags: vi.fn(),
  },
}));
vi.mock("posthog-js", () => ({ default: mocks.sdk }));
vi.mock("src/config-global", () => mocks.config);
vi.mock("src/utils/logger", () => ({ default: mocks.logger }));

beforeEach(() => {
  vi.resetModules();
  vi.resetAllMocks();
  mocks.config.POSTHOG_KEY = "test-key";
  localStorage.clear();
});

const user = {
  id: "user-1",
  name: "Test User",
  email: "test@example.test",
  organization: { id: "org-1", name: "Test Org" },
  default_workspace_id: "workspace-1",
  default_workspace_role: "admin",
  organization_role: "owner",
};

describe("PostHog analytics boundary", () => {
  it("keeps the existing SDK options and initializes once", async () => {
    const ph = await import("../posthog");
    await ph.initPostHog();
    await ph.initPostHog();
    expect(mocks.sdk.init).toHaveBeenCalledOnce();
    expect(mocks.sdk.init).toHaveBeenCalledWith(
      "test-key",
      expect.objectContaining({
        api_host: "https://us.i.posthog.com",
        cross_subdomain_cookie: true,
        cookie_domain: ".futureagi.com",
        autocapture: true,
        session_recording: expect.objectContaining({
          maskAllInputs: false,
          maskInputOptions: { password: true },
        }),
        capture_performance: true,
        capture_pageleave: true,
        capture_pageview: "history_change",
        advanced_disable_feature_flags: false,
        disable_session_recording: false,
        persistence: "localStorage+cookie",
        respect_dnt: true,
      }),
    );
  });

  it("coalesces auth provider legacy/direct identify and reset calls", async () => {
    const ph = await import("../posthog");
    const legacy = await import("../../Mixpanel");
    await ph.initPostHog();
    localStorage.setItem("utm_params", "utm_source=test&utm_campaign=example");
    legacy.identifyUser(user);
    ph.identifyPostHogUser(user);
    expect(mocks.sdk.identify).toHaveBeenCalledOnce();
    expect(mocks.sdk.identify).toHaveBeenCalledWith(
      user.id,
      {
        email: user.email,
        name: user.name,
        workspace_id: user.default_workspace_id,
        workspace_role: user.default_workspace_role,
        organization_role: user.organization_role,
      },
      { $initial_utm_source: "test", $initial_utm_campaign: "example" },
    );
    expect(mocks.sdk.group).toHaveBeenCalledTimes(2);
    legacy.resetUser();
    ph.resetPostHogUser();
    expect(mocks.sdk.reset).toHaveBeenCalledOnce();
    legacy.identifyUser(user);
    ph.identifyPostHogUser(user);
    expect(mocks.sdk.identify).toHaveBeenCalledTimes(2);
  });

  it("updates identity for workspace changes and resets standalone sessions", async () => {
    const ph = await import("../posthog");
    await ph.initPostHog();
    ph.resetPostHogUser();
    expect(mocks.sdk.reset).toHaveBeenCalledOnce();
    ph.identifyPostHogUser(user);
    ph.identifyPostHogUser({ ...user, default_workspace_id: "workspace-2" });
    expect(mocks.sdk.identify).toHaveBeenCalledTimes(2);
    ph.resetPostHogUser();
    expect(mocks.sdk.reset).toHaveBeenCalledTimes(2);
  });

  it("does not initialize or capture without a project key", async () => {
    mocks.config.POSTHOG_KEY = "";
    const ph = await import("../posthog");
    await ph.initPostHog();
    ph.trackPostHogEvent("example");
    ph.identifyPostHogUser(user);
    ph.resetPostHogUser();
    expect(ph.isFeatureEnabled("flag")).toBe(false);
    expect(ph.getFeatureFlagPayload("flag")).toBeNull();
    expect(mocks.sdk.init).not.toHaveBeenCalled();
    expect(mocks.sdk.capture).not.toHaveBeenCalled();
    expect(mocks.sdk.identify).not.toHaveBeenCalled();
    expect(mocks.sdk.reset).not.toHaveBeenCalled();
  });

  it("contains SDK failures and leaves identify retryable", async () => {
    const ph = await import("../posthog");
    await ph.initPostHog();
    mocks.sdk.identify.mockImplementationOnce(() => {
      throw new Error("SDK unavailable");
    });
    expect(() => ph.identifyPostHogUser(user)).not.toThrow();
    ph.identifyPostHogUser(user);
    expect(mocks.sdk.identify).toHaveBeenCalledTimes(2);
    mocks.sdk.capture.mockImplementationOnce(() => {
      throw new Error("SDK unavailable");
    });
    expect(() => ph.trackPostHogEvent("example")).not.toThrow();
    expect(mocks.logger.error).toHaveBeenCalledTimes(2);
  });
});

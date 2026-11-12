import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({
  init: vi.fn(),
  track: vi.fn(),
  set_config: vi.fn(),
  stop_session_recording: vi.fn(),
  get_config: vi.fn(),
  _recorder: {},
}));
vi.mock("mixpanel-browser", () => ({ default: sdk }));
vi.mock("src/config-global", () => ({ MIXPANEL_HOST: "" }));
vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), debug: vi.fn() },
}));
vi.mock("src/utils/sessionReplayPolicy", () => ({
  isSessionReplayBlockedPath: () => false,
}));

const SECRET = "fake-credential-for-unit-test";
const paths = [
  "/auth/jwt/invitation/accept/fake-user/",
  "/auth/jwt/invitation/set-password/fake-user/",
  "/auth/jwt/verify/fake-user/",
  "/shared/",
  "/review/",
];
const credentialUrls = [
  ...paths.map((prefix) => `${prefix}${SECRET}`),
  ...[
    "sso_token",
    "access_token",
    "refresh_token",
    "onboarding_token",
    "onboarding_gcp_token",
    "access",
    "refresh",
  ].map((param) => `/callback?${param}=${SECRET}&keep=1`),
];
const load = () => import("src/utils/Mixpanel/mixpanel");
const visit = (url) => window.history.replaceState(null, "", url);

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "fake-analytics-project-for-unit-test");
  vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "100");
  visit("/");
});
afterEach(() => {
  vi.unstubAllEnvs();
  visit("/");
});

describe("Mixpanel SDK-added URL defense", () => {
  it("disables persistence of the previous page's credential-bearing referrer", async () => {
    await load();
    expect(sdk.init.mock.calls[0][1].save_referrer).toBe(false);
  });

  it.each(credentialUrls)(
    "does not start replay or track on %s",
    async (url) => {
      visit(url);
      const { trackEvent, trackError } = await load();
      const config = sdk.init.mock.calls[0][1];
      expect(config.record_sessions_percent).toBe(0);
      // Installed mixpanel-core.js applies this after defaults/superprops merge,
      // including for identify events that skip before_send hooks.
      expect(config.property_blacklist).toEqual(
        expect.arrayContaining([
          "$current_url",
          "$referrer",
          "$initial_referrer",
        ]),
      );
      trackEvent("fake event", { count: 1 });
      trackError({ error: "fake error" });
      expect(sdk.track).not.toHaveBeenCalled();
    },
  );

  it.each(credentialUrls)(
    "stops existing replay when synchronizing %s",
    async (url) => {
      const { syncMixpanelSessionReplay, trackEvent } = await load();
      expect(sdk.init.mock.calls[0][1].record_sessions_percent).toBe(100);
      visit(url);
      syncMixpanelSessionReplay(window.location.pathname);
      expect(sdk.set_config).toHaveBeenCalledWith({
        record_sessions_percent: 0,
      });
      expect(sdk.stop_session_recording).toHaveBeenCalledTimes(1);
      trackEvent("fake event", {});
      expect(sdk.track).not.toHaveBeenCalled();
    },
  );

  it("keeps ordinary analytics and scrubs credential links in supplied properties", async () => {
    const { trackEvent } = await load();
    trackEvent("fake event", { destination: `/review/${SECRET}?keep=1` });
    expect(sdk.track).toHaveBeenCalledWith("fake event", {
      destination: "/review/auth-secret-redacted?keep=1",
    });
  });
});

// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  client: null,
  config: null,
  beforeInit: null,
  requests: [],
  init: vi.fn(),
}));
vi.mock("src/config-global", () => ({
  CURRENT_ENVIRONMENT: "production",
  POSTHOG_KEY: "fake-project-for-unit-test",
  POSTHOG_HOST: "https://analytics.example.invalid",
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/sessionReplayPolicy", () => ({
  SESSION_REPLAY_URL_BLOCKLIST: [],
}));
vi.mock("posthog-js", async () => {
  const { PostHog } = await import("posthog-js/lib/src/posthog-core.js");
  const { PostHogFeatureFlags } = await import(
    "posthog-js/lib/src/posthog-featureflags.js"
  );
  const { jsonStringify } = await import("posthog-js/lib/src/request.js");
  return {
    default: {
      capture: (...args) => state.client.capture(...args),
      identify: (...args) => state.client.identify(...args),
      group: (...args) => state.client.group(...args),
      init: (key, config) => {
        state.init();
        state.beforeInit = {
          local: window.localStorage.getItem(
            "ph_fake-project-for-unit-test_posthog",
          ),
          cookie: decodeURIComponent(document.cookie),
        };
        state.config = config;
        const client = new PostHog();
        // Intercept the installed SDK's last request boundary, before compression or I/O.
        client._send_request = (request) => {
          state.requests.push({
            url: request.url,
            data: JSON.parse(jsonStringify(request.data || {})),
            body: jsonStringify(request.data || {}),
          });
        };
        state.client = client;
        return client.init(key, {
          ...config,
          __extensionClasses: { featureFlags: PostHogFeatureFlags },
          disable_external_dependency_loading: true,
          request_batching: false,
          capture_performance: false,
          autocapture: false,
        });
      },
    },
  };
});

const secret = "fake-credential-for-unit-test";
const storageKey = "ph_fake-project-for-unit-test_posthog";
const seed = () => ({
  distinct_id: "fake-user-id",
  $initial_person_info: {
    u: `https://app.example.invalid/shared/${secret}?keep=1`,
    r: `https://app.example.invalid/callback?access_token=${secret}&keep=1`,
  },
  $initial_current_url: `/review/${secret}`,
  $initial_referrer: `/accounts/accept-invitation/fake-user/${secret}/`,
  $referrer: `/callback#refresh_token=${secret}`,
  $current_url: `/accounts/password-reset-confirm/fake-user/${secret}/`,
  $enabled_feature_flags: { safe_feature: true },
  safe_counter: 42,
});
const load = async () => {
  const app = await import("src/utils/PostHog/posthog");
  app.initPostHog();
  return app;
};
const cookieValue = () => {
  const cookie = document.cookie
    .split("; ")
    .find((value) => value.startsWith(`${storageKey}=`));
  return JSON.parse(decodeURIComponent(cookie.slice(storageKey.length + 1)));
};

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.clearAllMocks();
  state.requests = [];
  state.client = null;
  window.localStorage.clear();
  window.sessionStorage.clear();
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0].trim();
    for (const domain of [
      "",
      "; Domain=app.example.invalid",
      "; Domain=.example.invalid",
    ]) {
      document.cookie = `${name}=; Max-Age=0; Path=/${domain}`;
    }
  }
  window.history.replaceState(null, "", "/");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Network forbidden in SDK tests");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in SDK tests");
  });
  Object.defineProperty(window.navigator, "sendBeacon", {
    configurable: true,
    value: vi.fn(() => {
      throw new Error("Beacon forbidden in SDK tests");
    }),
  });
});
afterEach(() => {
  state.client?.stopSessionRecording();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("installed PostHog 1.360.2 persistence and flags", () => {
  it.each([
    `/callback?access_token=${secret}&refresh_token=${secret}`,
    `/shared/${secret}`,
    `/auth/jwt/invitation/accept/fake-user/${secret}`,
  ])("does not persist or send %s through flags", async (url) => {
    window.history.replaceState(null, "", url);
    await load();
    const ph = state.client;
    ph.capture("fake event");
    ph.featureFlags._requestInFlight = false;
    ph.featureFlags._callFlagsEndpoint();
    const flags = state.requests.filter((request) =>
      request.url.includes("/flags/"),
    );
    expect(flags.length).toBeGreaterThan(0);
    expect(JSON.stringify(flags.map((request) => request.data))).not.toContain(
      secret,
    );
    expect(JSON.stringify(ph.persistence.props)).not.toContain(secret);
    expect(window.localStorage.getItem(storageKey)).not.toContain(secret);
    expect(decodeURIComponent(document.cookie)).not.toContain(secret);
    expect(state.config.mask_personal_data_properties).toBe(true);
    expect(state.config.custom_personal_data_properties).toEqual(
      expect.arrayContaining([
        "access_token",
        "refresh_token",
        "sso_token",
        "onboarding_token",
        "onboarding_gcp_token",
        "access",
        "refresh",
      ]),
    );
    expect(state.config.disable_session_recording).toBe(true);
    // A reset and another capture must not reintroduce raw initial/referrer data.
    ph.reset();
    ph.capture("fake event after reset");
    expect(JSON.stringify(ph.persistence.props)).not.toContain(secret);
  });

  it("cleans seeded localStorage, sessionStorage and domain cookies BEFORE SDK initialization without dropping state", async () => {
    const data = seed();
    window.localStorage.setItem(storageKey, JSON.stringify(data));
    window.sessionStorage.setItem(storageKey, JSON.stringify(data));
    window.localStorage.setItem("unrelated-state", JSON.stringify(data));
    document.cookie = `${storageKey}=${encodeURIComponent(JSON.stringify(data))}; Path=/; Domain=.example.invalid; SameSite=Lax`;
    await load();
    expect(JSON.stringify(state.beforeInit)).not.toContain(secret);
    expect(window.sessionStorage.getItem(storageKey)).not.toContain(secret);
    const persisted = JSON.parse(window.localStorage.getItem(storageKey));
    expect(persisted.distinct_id).toBe(data.distinct_id);
    expect(persisted.safe_counter).toBe(42);
    expect(persisted.$enabled_feature_flags).toEqual(
      data.$enabled_feature_flags,
    );
    expect(persisted.$initial_person_info.u).toBe(
      "https://app.example.invalid/shared/auth-secret-redacted?keep=1",
    );
    expect(window.localStorage.getItem("unrelated-state")).toBe(
      JSON.stringify(data),
    );
    expect(cookieValue().distinct_id).toBe(data.distinct_id);
    expect(decodeURIComponent(document.cookie)).not.toContain(secret);
    state.client.featureFlags._requestInFlight = false;
    state.client.featureFlags._callFlagsEndpoint();
    const flags = state.requests.filter((request) =>
      request.url.includes("/flags/"),
    );
    expect(flags.length).toBeGreaterThan(0);
    expect(JSON.stringify(flags)).not.toContain(secret);
  });

  it.each([
    `/shared/${secret}`,
    `/auth/jwt/invitation/accept/fake-user/${secret}`,
    `/callback#ACCESS_TOKEN=${secret}`,
    `/callback;refresh_token=${secret}`,
  ])(
    "scrubs every storage write across session rotation and reset on %s",
    async (url) => {
      window.history.replaceState(null, "", url);
      vi.spyOn(document, "referrer", "get").mockReturnValue(
        `https://app.example.invalid/review/${secret}`,
      );
      const writes = [];
      const storagePrototype = Object.getPrototypeOf(window.localStorage);
      const setItem = storagePrototype.setItem;
      vi.spyOn(storagePrototype, "setItem").mockImplementation(
        function (key, value) {
          if (key === storageKey) writes.push(value);
          return setItem.call(this, key, value);
        },
      );
      await load();
      const ph = state.client;
      ph.capture("fake first session event");
      const firstSession = ph.persistence.props.$client_session_props.sessionId;
      vi.setSystemTime(Date.now() + 31 * 60 * 1000);
      ph.capture("fake rotated session event");
      expect(ph.persistence.props.$client_session_props.sessionId).not.toBe(
        firstSession,
      );
      ph.reset();
      ph.capture("fake reset session event");
      expect(writes.length).toBeGreaterThan(0);
      expect(JSON.stringify(writes)).not.toContain(secret);
      expect(JSON.stringify(ph.persistence.props)).not.toContain(secret);
      expect(ph.persistence.props.$client_session_props.props.r).toBe(
        "https://app.example.invalid/review/auth-secret-redacted",
      );
      expect(window.localStorage.getItem(storageKey)).not.toContain(secret);
    },
  );

  it.each(["capture", "identify", "group", "direct-flags"])(
    "scrubs ongoing %s properties before persistence and subsequent flags requests",
    async (operation) => {
      const app = await load();
      const ph = state.client;
      state.requests = [];
      const destination = `/shared/${secret}`;
      if (operation === "capture") {
        app.trackPostHogEvent("fake property event", {
          $set: { destination, safe_count: 42 },
        });
      } else if (operation === "identify") {
        app.identifyPostHogUser({ id: "fake-user", name: destination });
      } else if (operation === "group") {
        app.identifyPostHogUser({
          id: "fake-user",
          organization: { id: "fake-organization", name: destination },
        });
      } else {
        ph.setPersonPropertiesForFlags({ destination, safe_count: 42 }, false);
        ph.capture("fake direct override event");
      }
      const events = state.requests.filter((request) => request.data.event);
      expect(events.length).toBeGreaterThan(0);
      expect(JSON.stringify(events)).not.toContain(secret);
      ph.featureFlags._requestInFlight = false;
      ph.featureFlags._callFlagsEndpoint();
      const flags = state.requests.filter((request) =>
        request.url.includes("/flags/"),
      );
      expect(flags.length).toBeGreaterThan(0);
      expect(JSON.stringify(flags)).not.toContain(secret);
      expect(JSON.stringify(ph.persistence.props)).not.toContain(secret);
      expect(window.localStorage.getItem(storageKey)).not.toContain(secret);
      const latest = flags[flags.length - 1].data;
      if (operation === "capture" || operation === "direct-flags") {
        expect(latest.person_properties.destination).toBe(
          "/shared/auth-secret-redacted",
        );
        expect(latest.person_properties.safe_count).toBe(42);
      } else if (operation === "identify") {
        expect(latest.person_properties.name).toBe(
          "/shared/auth-secret-redacted",
        );
      } else {
        expect(latest.group_properties.organization.name).toBe(
          "/shared/auth-secret-redacted",
        );
      }
    },
  );

  it.each(["URL", "class-toJSON"])(
    "normalizes %s overrides before every persisted JSON and serialized flags body",
    async (kind) => {
      const writes = [];
      const storagePrototype = Object.getPrototypeOf(window.localStorage);
      const setItem = storagePrototype.setItem;
      vi.spyOn(storagePrototype, "setItem").mockImplementation(
        function (key, value) {
          if (key === storageKey) writes.push(value);
          return setItem.call(this, key, value);
        },
      );
      await load();
      const ph = state.client;
      class Destination {
        toJSON() {
          return `https://app.example.invalid/shared/${secret}?keep=1`;
        }
      }
      const destination =
        kind === "URL"
          ? new URL(`https://app.example.invalid/shared/${secret}?keep=1`)
          : new Destination();
      const clean =
        "https://app.example.invalid/shared/auth-secret-redacted?keep=1";
      ph.setPersonPropertiesForFlags({ destination, safe_count: 42 }, false);
      ph.setGroupPropertiesForFlags(
        { organization: { destination, safe_count: 42 } },
        false,
      );
      const stored = JSON.parse(window.localStorage.getItem(storageKey));
      expect(writes.length).toBeGreaterThan(0);
      expect(JSON.stringify(writes)).not.toContain(secret);
      expect(stored.$stored_person_properties.destination).toBe(clean);
      expect(stored.$stored_group_properties.organization.destination).toBe(
        clean,
      );
      expect(stored.$stored_person_properties.safe_count).toBe(42);
      expect(stored.distinct_id).toBe(ph.get_distinct_id());
      state.requests = [];
      ph.featureFlags._requestInFlight = false;
      ph.featureFlags._callFlagsEndpoint();
      const flags = state.requests.filter((request) =>
        request.url.includes("/flags/"),
      );
      expect(flags).toHaveLength(1);
      expect(flags[0].body).not.toContain(secret);
      expect(JSON.parse(flags[0].body).person_properties.destination).toBe(
        clean,
      );
      expect(
        JSON.parse(flags[0].body).group_properties.organization.destination,
      ).toBe(clean);
      expect(JSON.parse(flags[0].body).person_properties.safe_count).toBe(42);
      // A caller can also retain/mutate a property without invoking save().
      // The independent flags send boundary must normalize that late value.
      ph.persistence.props.$stored_person_properties.destination = destination;
      state.requests = [];
      ph.featureFlags._requestInFlight = false;
      ph.featureFlags._callFlagsEndpoint();
      expect(state.requests).toHaveLength(1);
      expect(state.requests[0].body).not.toContain(secret);
      expect(
        JSON.parse(state.requests[0].body).person_properties.destination,
      ).toBe(clean);
    },
  );

  it("fails closed rather than initializing when existing persistence cannot be inspected", async () => {
    window.localStorage.setItem(storageKey, "not-json");
    await load();
    expect(state.init).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(storageKey)).toBe("not-json");
  });
});

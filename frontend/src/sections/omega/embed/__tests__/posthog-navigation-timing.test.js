// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WebVitalsAutocapture } from "posthog-js/lib/src/extensions/web-vitals/index.js";
import { LazyLoadedSessionRecording } from "posthog-js/lib/src/extensions/replay/external/lazy-loaded-session-recorder.js";
import { buildNetworkRequestOptions } from "posthog-js/lib/src/extensions/replay/external/config.js";

const state = vi.hoisted(() => ({ config: null }));
vi.mock("posthog-js", () => ({
  default: {
    init: (_key, config) => {
      state.config = config;
    },
  },
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

const secret = "fake-navigation-credential-for-unit-test";
const originalExtensions = Object.getOwnPropertyDescriptor(
  window,
  "__PosthogExtensions__",
);
const originalNavigationGetter = Object.getOwnPropertyDescriptor(
  window.performance,
  "getEntriesByType",
);
const installExtensions = (extensions) => {
  Object.defineProperty(window, "__PosthogExtensions__", {
    configurable: true,
    value: extensions,
  });
};
const loadConfig = async () => {
  const { initPostHog } = await import("src/utils/PostHog/posthog");
  initPostHog();
  expect(state.config).not.toBeNull();
  return state.config;
};
// Model the browser-owned entry: replacing history must not rewrite its name.
class NavigationEntry {
  constructor(name) {
    Object.defineProperty(this, "name", { value: name, enumerable: true });
    this.entryType = "navigation";
    this.startTime = 0;
    this.responseEnd = 20;
  }

  toJSON() {
    return { ...this };
  }
}
const retainedNavigation = (path) => {
  window.history.replaceState(null, "", path);
  const entry = new NavigationEntry(window.location.href);
  Object.defineProperty(window.performance, "getEntriesByType", {
    configurable: true,
    value: (type) => (type === "navigation" ? [entry] : []),
  });
  window.history.replaceState(null, "", "/callback?keep=1");
  return entry;
};

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  state.config = null;
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  // Fail closed before loading application wiring; no SDK transport may run.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Network forbidden in navigation timing tests");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in navigation timing tests");
  });
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (originalExtensions) {
    Object.defineProperty(window, "__PosthogExtensions__", originalExtensions);
  } else {
    delete window.__PosthogExtensions__;
  }
  if (originalNavigationGetter) {
    Object.defineProperty(
      window.performance,
      "getEntriesByType",
      originalNavigationGetter,
    );
  } else {
    delete window.performance.getEntriesByType;
  }
  window.history.replaceState(null, "", "/");
});

describe("installed PostHog 1.360.2 original navigation timing channels", () => {
  it.each([
    `/callback?access_token=${secret}&refresh_token=${secret}`,
    `/shared/${secret}`,
  ])("keeps web vitals disabled after remote opt-in for %s", async (path) => {
    const entry = retainedNavigation(path);
    const config = await loadConfig();
    const registerMetric = vi.fn((callback) => {
      callback({
        name: "FCP",
        value: 20,
        entries: [entry],
        attribution: { navigationEntry: entry },
      });
    });
    installExtensions({
      postHogWebVitalsCallbacks: {
        onLCP: registerMetric,
        onCLS: registerMetric,
        onFCP: registerMetric,
        onINP: registerMetric,
      },
    });
    const capture = vi.fn();
    const vitals = new WebVitalsAutocapture({
      config,
      persistence: { props: {}, register: vi.fn() },
      sessionManager: {
        checkAndGetSessionAndWindowId: () => ({
          sessionId: "fake-session",
          windowId: "fake-window",
        }),
      },
      capture,
    });
    vitals.onRemoteConfig({ capturePerformance: { web_vitals: true } });
    vi.runOnlyPendingTimers();
    expect(window.location.href).not.toContain(secret);
    expect(window.performance.getEntriesByType("navigation")[0].name).toBe(
      entry.name,
    );
    expect(entry.name).toContain(secret);
    expect(registerMetric).not.toHaveBeenCalled();
    expect(capture).not.toHaveBeenCalled();
    expect(vitals.isEnabled).toBe(false);
  });

  it("does not install the replay timing plugin after remote performance opt-in", async () => {
    retainedNavigation(`/callback?access_token=${secret}`);
    const config = await loadConfig();
    const networkPlugin = vi.fn(() => ({ name: "fake-network-plugin" }));
    installExtensions({
      rrwebPlugins: { getRecordNetworkPlugin: networkPlugin },
    });
    // Use the installed getter/plugin-selection code without starting rrweb,
    // timers, DOM recording, or remote configuration downloads.
    const recorder = Object.create(LazyLoadedSessionRecording.prototype);
    recorder._instance = {
      config,
      get_property: () => ({
        networkPayloadCapture: { capturePerformance: true },
      }),
    };
    expect(recorder._gatherRRWebPlugins()).toEqual([]);
    expect(networkPlugin).not.toHaveBeenCalled();
    expect(recorder._networkPayloadCapture).toBeUndefined();
  });

  it("disables initial timing capture and retains masking if remote headers keep a network plugin active", async () => {
    const entry = retainedNavigation(`/callback?access_token=${secret}&keep=1`);
    const config = await loadConfig();
    const options = buildNetworkRequestOptions(config, {
      recordPerformance: true,
      recordHeaders: true,
    });
    // Remote body/header recording can still cause the SDK to observe timing
    // entries. Keep its existing pre-compression URL scrubber as a defense.
    const masked = options.maskRequestFn(entry.toJSON());
    expect(masked.name).toBe(
      "https://app.example.invalid/callback?access_token=auth-secret-redacted&keep=1",
    );
    expect(JSON.stringify(masked)).not.toContain(secret);
    expect(entry.name).toContain(secret);
    expect(options.recordPerformance).toBe(false);
    expect(options.recordInitialRequests).toBe(false);
  });
});

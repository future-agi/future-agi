// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildNetworkRequestOptions } from "posthog-js/lib/src/extensions/replay/external/config.js";
import { getRecordNetworkPlugin } from "posthog-js/lib/src/extensions/replay/external/network-plugin.js";

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

const refresh = "fake-refresh-for-unit-test";
const access = "fake-access-for-unit-test";
const password = "fake-password-for-unit-test";
const requestHeader = "fake-request-header-for-unit-test";
const responseHeader = "fake-response-header-for-unit-test";
const responseBody = JSON.stringify({ access, refresh });
const cases = [
  ["refresh", "/accounts/token/refresh/", JSON.stringify({ refresh })],
  [
    "login",
    "/accounts/login/",
    JSON.stringify({ email: "user@example.invalid", password }),
  ],
];
const remoteOptions = { recordBody: true, recordHeaders: true };
const loadConfig = async () => {
  const { initPostHog } = await import("src/utils/PostHog/posthog");
  initPostHog();
  expect(state.config).not.toBeNull();
  return state.config;
};

// Use the installed option builder AND observer/serialization/masking path.
// Only browser timing delivery and the HTTP endpoints are in-memory doubles.
const captureNetwork = async (config, transport, path, body) => {
  const url = `https://app.example.invalid${path}`;
  const sent = [];
  const entry = {
    name: url,
    entryType: "resource",
    initiatorType: transport,
    startTime: 1,
    responseEnd: 2,
    toJSON() {
      return {
        name: this.name,
        entryType: this.entryType,
        initiatorType: this.initiatorType,
        startTime: this.startTime,
        responseEnd: this.responseEnd,
      };
    },
  };
  let deliverTiming;
  const disconnect = vi.fn();
  class OfflinePerformanceObserver {
    static supportedEntryTypes = ["resource"];

    constructor(callback) {
      deliverTiming = () => callback({ getEntries: () => [entry] });
    }

    observe() {}

    disconnect() {
      disconnect();
    }
  }
  vi.stubGlobal("PerformanceObserver", OfflinePerformanceObserver);
  class OfflineXHR extends EventTarget {
    constructor() {
      super();
      this.headers = {};
      this.DONE = 4;
      this.readyState = 0;
      this.status = 200;
      this.response = responseBody;
    }

    open() {
      this.readyState = 1;
    }

    setRequestHeader(name, value) {
      this.headers[name] = value;
    }

    getAllResponseHeaders() {
      return `content-type: application/json\r\nx-fixture-response: ${responseHeader}`;
    }

    send(requestBody) {
      sent.push({ body: requestBody, headers: { ...this.headers } });
      this.readyState = this.DONE;
      this.dispatchEvent(new Event("readystatechange"));
    }
  }
  const win = {
    PerformanceObserver: OfflinePerformanceObserver,
    performance: {
      now: () => 1,
      getEntriesByName: () => [entry],
    },
    XMLHttpRequest: OfflineXHR,
    fetch: async (input, init) => {
      const request = new Request(input, init);
      sent.push({
        body: await request.text(),
        headers: Object.fromEntries(request.headers),
      });
      return new Response(responseBody, {
        status: 200,
        headers: {
          "content-type": "application/json",
          "x-fixture-response": responseHeader,
        },
      });
    },
  };
  const mask = vi.fn(config.session_recording.maskCapturedNetworkRequestFn);
  const options = buildNetworkRequestOptions(
    {
      ...config,
      session_recording: {
        ...config.session_recording,
        maskCapturedNetworkRequestFn: mask,
      },
    },
    remoteOptions,
  );
  const plugin = getRecordNetworkPlugin(options);
  let captured;
  const recording = new Promise((resolve) => {
    captured = resolve;
  });
  const stop = plugin.observer(captured, win, plugin.options);
  try {
    const headers = {
      "content-type": "application/json",
      "x-fixture-request": requestHeader,
    };
    if (transport === "fetch") {
      const response = await win.fetch(url, { method: "POST", headers, body });
      expect(await response.text()).toBe(responseBody);
    } else {
      const xhr = new win.XMLHttpRequest();
      xhr.open("POST", url);
      for (const [name, value] of Object.entries(headers)) {
        xhr.setRequestHeader(name, value);
      }
      xhr.send(body);
      expect(xhr.response).toBe(responseBody);
    }
    // With local capture disabled, timings still reach the custom mask. With
    // capture enabled, the SDK filters these and emits its wrapped HTTP result.
    deliverTiming();
    const { requests } = await recording;
    expect(requests).toHaveLength(1);
    expect(requests[0].name).toBe(url);
    expect(mask).toHaveBeenCalledTimes(1);
    expect(sent).toEqual([{ body, headers }]);
    return requests[0];
  } finally {
    stop();
    expect(disconnect).toHaveBeenCalledTimes(1);
  }
};

beforeEach(() => {
  vi.resetModules();
  state.config = null;
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/dashboard/settings");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Network forbidden in replay payload tests");
    }),
  );
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(() => {
    throw new Error("XHR forbidden in replay payload tests");
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("installed PostHog 1.360.2 replay network payloads", () => {
  it("locally vetoes remote body/header opt-in without disabling replay", async () => {
    const config = await loadConfig();
    expect(config.session_recording.recordBody).toBe(false);
    expect(config.session_recording.recordHeaders).toBe(false);
    expect(config.disable_session_recording).toBe(false);
    expect(config.session_recording.maskCapturedNetworkRequestFn).toEqual(
      expect.any(Function),
    );
    const options = buildNetworkRequestOptions(config, remoteOptions);
    expect(options.recordBody).toBe(false);
    expect(options.recordHeaders).toBe(false);
  });

  describe.each(["fetch", "xmlhttprequest"])("%s", (transport) => {
    it.each(cases)(
      "keeps %s request/response bodies and headers out of replay despite remote opt-in",
      async (_label, path, body) => {
        const config = await loadConfig();
        // Positive control: the real SDK bypasses scrubPayloads for our custom
        // mask. Prove this harness exposes the raw payload when capture is on.
        const exposed = await captureNetwork(
          {
            ...config,
            session_recording: {
              ...config.session_recording,
              recordBody: true,
              recordHeaders: true,
            },
          },
          transport,
          path,
          body,
        );
        expect(exposed.requestBody).toBe(body);
        expect(exposed.responseBody).toBe(responseBody);
        expect(exposed.requestHeaders["x-fixture-request"]).toBe(requestHeader);
        expect(exposed.responseHeaders["x-fixture-response"]).toBe(
          responseHeader,
        );

        const recorded = await captureNetwork(config, transport, path, body);
        expect(recorded.requestBody).toBeUndefined();
        expect(recorded.responseBody).toBeUndefined();
        expect(recorded.requestHeaders).toBeUndefined();
        expect(recorded.responseHeaders).toBeUndefined();
        for (const fixture of [
          refresh,
          access,
          password,
          requestHeader,
          responseHeader,
        ]) {
          expect(JSON.stringify(recorded)).not.toContain(fixture);
        }
      },
    );
  });
});

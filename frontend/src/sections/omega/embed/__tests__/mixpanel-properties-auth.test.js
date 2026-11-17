// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ client: null, sent: [], writes: [], n: 0 }));
vi.mock("src/config-global", () => ({ MIXPANEL_HOST: "" }));
vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), debug: vi.fn() },
}));
vi.mock("src/utils/sessionReplayPolicy", () => ({
  isSessionReplayBlockedPath: () => false,
}));
vi.mock("mixpanel-browser", async () => {
  const { init_as_module } = await import(
    "mixpanel-browser/src/mixpanel-core.js"
  );
  return {
    default: {
      init: (key, config) => {
        state.client = init_as_module().init(
          key,
          {
            ...config,
            debug: false,
            api_host: "https://analytics.example.invalid",
            api_payload_format: "json",
            batch_requests: true,
            batch_autostart: false,
            record_sessions_percent: 0,
          },
          `sec588_properties_${(state.n += 1)}`,
        );
        // Keep the installed track, truncation, queue, storage and JSON encoder.
        // Intercept only the final I/O boundary, AFTER request serialization.
        vi.spyOn(state.client, "_send_request").mockImplementation(
          (url, data) => {
            state.sent.push({ url, body: data.data });
            return true;
          },
        );
      },
      track: (...args) => state.client.track(...args),
    },
  };
});

const secret = "fake-credential-for-unit-test";
const rawUrl = `https://app.example.invalid/accounts/password-reset-confirm/fake-user/${secret}/`;
const cleanUrl = rawUrl.replace(secret, "auth-secret-redacted");
const beaconDescriptor = Object.getOwnPropertyDescriptor(
  window.navigator,
  "sendBeacon",
);
class Destination {
  constructor() {
    this.url = rawUrl;
    this.safe_count = 42;
  }
}
class JsonDestination {
  toJSON() {
    return { url: rawUrl, safe_count: 42 };
  }
}
const propertyCases = [
  ["class", () => new Destination(), { url: cleanUrl, safe_count: 42 }],
  [
    "Error",
    () => Object.assign(new Error("ordinary error"), new Destination()),
    { url: cleanUrl, safe_count: 42 },
  ],
  ["URL", () => new URL(rawUrl), cleanUrl],
  [
    "class-toJSON",
    () => new JsonDestination(),
    { url: cleanUrl, safe_count: 42 },
  ],
  ["object-toJSON", () => ({ toJSON: () => rawUrl }), cleanUrl],
  [
    "array",
    () => [new Destination(), new URL(rawUrl)],
    [{ url: cleanUrl, safe_count: 42 }, cleanUrl],
  ],
];

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  state.client = null;
  state.sent = [];
  state.writes = [];
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/dashboard/projects");
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "fake-analytics-project-for-unit-test");
  vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "0");
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
  const storagePrototype = Object.getPrototypeOf(window.localStorage);
  const setItem = storagePrototype.setItem;
  vi.spyOn(storagePrototype, "setItem").mockImplementation(
    function (key, value) {
      state.writes.push(String(value));
      return setItem.call(this, key, value);
    },
  );
});
afterEach(() => {
  state.client?.stop_batch_senders();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  if (beaconDescriptor) {
    Object.defineProperty(window.navigator, "sendBeacon", beaconDescriptor);
  } else {
    delete window.navigator.sendBeacon;
  }
  window.history.replaceState(null, "", "/");
});

const sentEvent = (batched) => {
  if (batched) {
    const { events } = state.client.request_batchers;
    const queued = events.queue.memQueue;
    expect(queued).toHaveLength(1);
    expect(JSON.stringify(queued)).not.toContain(secret);
    expect(JSON.stringify(queued)).toContain("auth-secret-redacted");
    const persisted = JSON.parse(
      window.localStorage.getItem(events.queue.storageKey),
    );
    expect(persisted).toHaveLength(1);
    expect(persisted[0].payload).toEqual(queued[0].payload);
    expect(state.sent).toHaveLength(0);
    events.flush();
  }
  expect(state.sent).toHaveLength(1);
  expect(state.sent[0].body).not.toContain(secret);
  expect(state.sent[0].body).toContain("auth-secret-redacted");
  expect(state.writes.length).toBeGreaterThan(0);
  expect(JSON.stringify(state.writes)).not.toContain(secret);
  const payload = JSON.parse(state.sent[0].body);
  return batched ? payload[0] : payload;
};

for (const batched of [false, true]) {
  describe(`installed Mixpanel 2.53.0 properties, batching=${batched}`, () => {
    for (const operation of ["trackEvent", "trackError"]) {
      it.each(propertyCases)(
        `${operation} normalizes %s before persistence and send on a safe route`,
        async (_kind, create, expected) => {
          const app = await import("src/utils/Mixpanel/mixpanel");
          expect(state.client._batch_requests).toBe(true);
          state.client._batch_requests = batched;
          const properties = { details: create(), safe_count: 7 };
          if (operation === "trackEvent") {
            app.trackEvent("fake property event", properties);
          } else {
            app.trackError(properties, "safe operation");
          }
          const event = sentEvent(batched);
          expect(event.properties.details).toEqual(expected);
          expect(event.properties.safe_count).toBe(7);
          expect(event.event).toBe(
            operation === "trackEvent"
              ? "fake property event"
              : "Client Error : safe operation",
          );
        },
      );
    }

    it("bounds depth, cuts cycles and retains safe siblings and repeated aliases", async () => {
      const { trackEvent } = await import("src/utils/Mixpanel/mixpanel");
      state.client._batch_requests = batched;
      const shared = new Destination();
      shared.self = shared;
      let deep = new Destination();
      for (let index = 0; index < 64; index += 1) deep = { next: deep };
      trackEvent("fake bounded event", {
        first: shared,
        second: shared,
        deep,
        broken: {
          toJSON() {
            throw new Error("Cannot serialize fixture");
          },
        },
        safe_count: 7,
      });
      const { properties } = sentEvent(batched);
      expect(properties.first).toEqual({
        url: cleanUrl,
        safe_count: 42,
        self: null,
      });
      expect(properties.second).toEqual(properties.first);
      expect(properties.safe_count).toBe(7);
      expect(properties).not.toHaveProperty("broken");
      let cursor = properties.deep;
      let depth = 0;
      while (cursor && depth < 65) {
        cursor = cursor.next;
        depth += 1;
      }
      expect(cursor).toBeNull();
      expect(depth).toBeLessThanOrEqual(32);
      expect(shared.url).toBe(rawUrl);
      expect(shared.self).toBe(shared);
    });
  });
}

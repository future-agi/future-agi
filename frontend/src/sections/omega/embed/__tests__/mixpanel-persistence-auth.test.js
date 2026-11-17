// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  client: null,
  sent: [],
  writes: [],
  beforeInit: null,
  initCalls: 0,
  nextInstance: 0,
}));
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
        state.initCalls += 1;
        state.beforeInit = {
          persistence: window.localStorage.getItem(`mp_${key}_mixpanel`),
          queues: Object.fromEntries(
            ["ev", "pp", "gr"].map((suffix) => [
              suffix,
              window.localStorage.getItem(`__mpq_${key}_${suffix}`),
            ]),
          ),
          cookies: decodeURIComponent(document.cookie),
          writes: [...state.writes],
        };
        const sdk = init_as_module();
        // Install the I/O spy BEFORE init: the real batchers flush orphaned
        // storage synchronously during init, bypassing before_send hooks.
        vi.spyOn(
          Object.getPrototypeOf(sdk),
          "_send_request",
        ).mockImplementation((url, data, _options, callback) => {
          state.sent.push({ url, body: data.data, callback });
          return true;
        });
        state.client = sdk.init(
          key,
          {
            ...config,
            debug: false,
            api_host: "https://analytics.example.invalid",
            api_payload_format: "json",
            record_sessions_percent: 0,
          },
          // Manual mocks retain the core's named-instance registry across
          // resetModules; each case must exercise a fresh SDK initialization.
          `sec588_persistence_${state.nextInstance++}`,
        );
      },
      track: (...args) => state.client.track(...args),
    },
  };
});

const project = "fake-analytics-project-for-unit-test";
const storageKey = `mp_${project}_mixpanel`;
const queueKey = (suffix) => `__mpq_${project}_${suffix}`;
const secret = "fake-credential-for-unit-test";
const queryUrl = `https://app.example.invalid/callback?access_token=${secret}&keep=1`;
const pathUrl = `https://app.example.invalid/accounts/password-reset-confirm/fake-user/${secret}/`;
const encode = (url, depth) => {
  for (let index = 0; index < depth; index += 1) url = encodeURIComponent(url);
  return url;
};
const identities = {
  distinct_id: "fake-distinct-id",
  $device_id: "fake-device-id",
  $user_id: "fake-user-id",
  safe_count: 42,
};
const entry = (id, payload) => ({
  id,
  flushAfter: Date.now() - 60000,
  payload,
});
const payload = (suffix, properties) =>
  suffix === "ev"
    ? {
        event: "fake upgrade event",
        properties: { ...identities, ...properties },
      }
    : {
        $token: project,
        $distinct_id: identities.distinct_id,
        ...(suffix === "gr" && { $group_key: "org", $group_id: "fake-org-id" }),
        $set: { ...identities, ...properties },
      };
const load = () => import("src/utils/Mixpanel/mixpanel");
const beaconDescriptor = Object.getOwnPropertyDescriptor(
  window.navigator,
  "sendBeacon",
);
const clearCookies = () => {
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0].trim();
    for (const domain of [
      "",
      "; Domain=app.example.invalid",
      "; Domain=example.invalid",
    ]) {
      document.cookie = `${name}=; Max-Age=0; Path=/${domain}`;
    }
  }
};

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  state.client = null;
  state.sent = [];
  state.writes = [];
  state.beforeInit = null;
  state.initCalls = 0;
  window.localStorage.clear();
  window.sessionStorage.clear();
  clearCookies();
  window.history.replaceState(null, "", "/dashboard/projects");
  vi.stubEnv("VITE_MIXPANEL_TOKEN", project);
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
      state.writes.push({ key, value: String(value) });
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
  clearCookies();
  window.history.replaceState(null, "", "/");
});

describe("installed Mixpanel 2.53.0 upgrade persistence", () => {
  for (const suffix of ["ev", "pp", "gr"]) {
    it.each([
      ["raw query", queryUrl],
      ["raw backend path", pathUrl],
      ["encoded path", encode(pathUrl, 2)],
      ["encoded query", encode(queryUrl, 8)],
    ])(
      `rejects %s orphaned ${suffix} entries before autostart and retries`,
      async (_kind, url) => {
        const safe = entry(
          "safe-batch-id",
          payload(suffix, { keep: "ordinary event" }),
        );
        const backlog = [
          entry("url-batch-id", payload(suffix, { $current_url: url })),
          entry(
            "property-batch-id",
            payload(suffix, { supplied: { nested: [url] } }),
          ),
          entry(
            "key-batch-id",
            payload(suffix, { [url]: "credential-bearing key" }),
          ),
          safe,
        ];
        window.localStorage.setItem(queueKey(suffix), JSON.stringify(backlog));
        state.writes = [];
        await load();
        expect(state.client.get_config("batch_autostart")).toBe(true);
        expect(state.client._batch_requests).toBe(true);
        expect(JSON.parse(state.beforeInit.queues[suffix])).toEqual([safe]);
        expect(
          JSON.parse(window.localStorage.getItem(queueKey(suffix))),
        ).toEqual([safe]);
        expect(state.sent).toHaveLength(1);
        expect(JSON.parse(state.sent[0].body)).toEqual([
          suffix === "ev"
            ? {
                ...safe.payload,
                properties: {
                  ...safe.payload.properties,
                  mp_sent_by_lib_version: "2.53.0",
                },
              }
            : safe.payload,
        ]);
        expect(JSON.stringify(state.writes)).not.toContain(secret);
        expect(JSON.stringify(state.sent)).not.toContain(secret);
        // Leave the backlog persisted on a retriable response, then exercise the
        // SDK's real retry. No fresh caller wrapper or send hook sees these items.
        state.sent[0].callback({ status: 0, xhr_req: { status: 503 } });
        vi.advanceTimersByTime(10000);
        expect(state.sent).toHaveLength(2);
        expect(state.sent[1].body).toBe(state.sent[0].body);
        expect(
          JSON.parse(window.localStorage.getItem(queueKey(suffix))),
        ).toEqual([safe]);
        expect(JSON.stringify(state.writes)).not.toContain(secret);
      },
    );
  }

  it.each(["localStorage", "host cookie", "domain cookie"])(
    "scrubs %s superproperties before any send or SDK save, preserving IDs",
    async (store) => {
      const data = {
        ...identities,
        $initial_referrer: queryUrl,
        $initial_referring_domain: pathUrl,
        supplied: { [encode(pathUrl, 2)]: encode(queryUrl, 2), safe: true },
      };
      if (store === "localStorage") {
        window.localStorage.setItem(storageKey, JSON.stringify(data));
      } else {
        const domain =
          store === "domain cookie" ? "; Domain=example.invalid" : "";
        document.cookie = `${storageKey}=${encodeURIComponent(JSON.stringify(data))}; Path=/${domain}`;
      }
      // A pending ordinary batch forces a send during init, before the
      // persistence constructor has had an opportunity to mask dirty storage.
      const safe = entry("safe-init-id", payload("ev", { keep: true }));
      window.localStorage.setItem(queueKey("ev"), JSON.stringify([safe]));
      state.writes = [];
      const { trackEvent } = await load();
      expect(state.sent).toHaveLength(1);
      expect(JSON.stringify(state.beforeInit)).not.toContain(secret);
      expect(state.client.persistence.props).toMatchObject(identities);
      expect(state.client.persistence.props.$initial_referrer).toBe(
        queryUrl.replace(secret, "auth-secret-redacted"),
      );
      expect(state.client.persistence.props.$initial_referring_domain).toBe(
        pathUrl.replace(secret, "auth-secret-redacted"),
      );
      expect(state.client.persistence.props.supplied).toEqual({
        [encode(pathUrl.replace(secret, "auth-secret-redacted"), 2)]: encode(
          queryUrl.replace(secret, "auth-secret-redacted"),
          2,
        ),
        safe: true,
      });
      expect(JSON.stringify(state.writes)).not.toContain(secret);
      expect(window.localStorage.getItem(storageKey)).not.toContain(secret);
      expect(decodeURIComponent(document.cookie)).not.toContain(secret);
      state.client._batch_requests = false;
      trackEvent("fake post-upgrade event", { safe: true });
      expect(state.sent).toHaveLength(2);
      expect(JSON.parse(state.sent[1].body).properties).toMatchObject(
        identities,
      );
      expect(JSON.stringify(state.sent)).not.toContain(secret);
    },
  );

  it("does not rewrite clean blobs or inspect another project's storage", async () => {
    const clean = JSON.stringify(identities, null, 2);
    const safe = JSON.stringify(
      [entry("safe-id", payload("ev", { keep: true }))],
      null,
      2,
    );
    window.localStorage.setItem(storageKey, clean);
    window.localStorage.setItem(queueKey("ev"), safe);
    window.localStorage.setItem("__mpq_other-project_ev", "not-json");
    window.localStorage.setItem("mp_other-project_mixpanel", "not-json");
    window.localStorage.setItem("unrelated-state", queryUrl);
    state.writes = [];
    await load();
    expect(state.beforeInit.writes).toEqual([]);
    expect(state.beforeInit.persistence).toBe(clean);
    expect(state.beforeInit.queues.ev).toBe(safe);
    expect(window.localStorage.getItem("__mpq_other-project_ev")).toBe(
      "not-json",
    );
    expect(window.localStorage.getItem("mp_other-project_mixpanel")).toBe(
      "not-json",
    );
    expect(window.localStorage.getItem("unrelated-state")).toBe(queryUrl);
    expect(state.client.persistence.props).toMatchObject(identities);
  });

  it.each([
    [storageKey, "not-json"],
    [queueKey("ev"), "{}"],
    [queueKey("pp"), "[null]"],
  ])(
    "skips init when project persistence cannot be inspected (%s)",
    async (key, raw) => {
      window.localStorage.setItem(key, raw);
      await load();
      expect(state.initCalls).toBe(0);
      expect(state.sent).toEqual([]);
      expect(window.localStorage.getItem(key)).toBe(raw);
    },
  );

  it("skips init when a dirty backlog cannot be rewritten", async () => {
    const key = queueKey("ev");
    window.localStorage.setItem(
      key,
      JSON.stringify([
        entry("dirty-id", payload("ev", { $current_url: queryUrl })),
      ]),
    );
    const prototype = Object.getPrototypeOf(window.localStorage);
    const setItem = prototype.setItem.getMockImplementation();
    vi.spyOn(prototype, "setItem").mockImplementation(function (name, value) {
      if (name !== key) return setItem.call(this, name, value);
      return undefined;
    });
    await load();
    expect(state.initCalls).toBe(0);
    expect(state.sent).toEqual([]);
  });

  it("skips init when project storage is inaccessible", async () => {
    const prototype = Object.getPrototypeOf(window.localStorage);
    const getItem = prototype.getItem;
    vi.spyOn(prototype, "getItem").mockImplementation(function (key) {
      if (key === storageKey) throw new Error("Storage unavailable");
      return getItem.call(this, key);
    });
    await load();
    expect(state.initCalls).toBe(0);
    expect(state.sent).toEqual([]);
  });
});

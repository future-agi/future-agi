// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://app.eu.example.invalid/"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  client: null,
  nextInstance: 0,
  initCalls: 0,
  beforeInit: null,
  cookieWrites: [],
  storageWrites: [],
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
  const { _ } = await import("mixpanel-browser/src/utils.js");
  return {
    default: {
      init: (key, config) => {
        state.initCalls += 1;
        state.beforeInit = {
          persistence: window.localStorage.getItem(`mp_${key}_mixpanel`),
          cookies: document.cookie,
          cookieWrites: [...state.cookieWrites],
          storageWrites: [...state.storageWrites],
        };
        // The hoisted factory retains the SDK's storage-support cache as well
        // as its instance registry. Recheck support under this case's spies.
        _.localStorage.is_supported(true);
        const sdk = init_as_module();
        vi.spyOn(Object.getPrototypeOf(sdk), "_send_request").mockReturnValue(
          true,
        );
        state.client = sdk.init(
          key,
          {
            ...config,
            debug: false,
            api_host: "https://analytics.example.invalid",
            record_sessions_percent: 0,
          },
          `sec588_cookie_reset_${state.nextInstance++}`,
        );
      },
    },
  };
});

const project = "fake-cookie-project-for-unit-test";
const storageKey = `mp_${project}_mixpanel`;
const secret = "fake-cookie-credential-for-unit-test";
const dirtyUrl = `https://app.eu.example.invalid/callback?access_token=${secret}`;
const cookieProps = {
  distinct_id: "fake-previous-distinct-id",
  $device_id: "fake-previous-device-id",
  $user_id: "fake-previous-user-id",
  "Org Id": "fake-previous-org-id",
  supplied: dirtyUrl,
};
const domains = [
  "",
  "; Domain=app.eu.example.invalid",
  "; Domain=eu.example.invalid",
  "; Domain=example.invalid",
];
const seedCookie = (domain = "", props = cookieProps) => {
  document.cookie = `${storageKey}=${encodeURIComponent(JSON.stringify(props))}; Path=/${domain}`;
};
const clearCookies = () => {
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0].trim();
    for (const domain of domains) {
      document.cookie = `${name}=; Max-Age=0; Path=/${domain}`;
    }
  }
};
const load = () => import("src/utils/Mixpanel/mixpanel");
const beaconDescriptor = Object.getOwnPropertyDescriptor(
  window.navigator,
  "sendBeacon",
);

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  state.client = null;
  state.initCalls = 0;
  state.beforeInit = null;
  state.cookieWrites = [];
  state.storageWrites = [];
  window.localStorage.clear();
  window.sessionStorage.clear();
  clearCookies();
  window.history.replaceState(null, "", "/dashboard/projects");
  vi.stubEnv("VITE_MIXPANEL_TOKEN", project);
  vi.stubEnv("VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "0");
  vi.stubGlobal("Appcues", { identify: vi.fn(), reset: vi.fn() });
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
  const prototype = Object.getPrototypeOf(window.localStorage);
  const setItem = prototype.setItem;
  vi.spyOn(prototype, "setItem").mockImplementation(function (key, value) {
    state.storageWrites.push({ key, value: String(value) });
    return setItem.call(this, key, value);
  });
  const { set } = Object.getOwnPropertyDescriptor(Document.prototype, "cookie");
  vi.spyOn(document, "cookie", "set").mockImplementation(function (value) {
    state.cookieWrites.push(value);
    set.call(this, value);
  });
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

const reload = async () => {
  state.client.stop_batch_senders();
  state.client = null;
  vi.resetModules();
  await load();
};

describe("Mixpanel cookie migration logout regression", () => {
  it.each(domains)(
    "does not resurrect a previous user after reset and reload (%s)",
    async (domain) => {
      seedCookie(domain);
      await load();
      expect(state.initCalls).toBe(1);
      expect(state.client.persistence.props).toMatchObject({
        $user_id: cookieProps.$user_id,
        "Org Id": cookieProps["Org Id"],
      });
      state.client.reset();
      expect(state.client.persistence.props.$user_id).toBeUndefined();
      expect(state.client.persistence.props["Org Id"]).toBeUndefined();
      await reload();
      expect(state.initCalls).toBe(2);
      expect(state.client.persistence.props.$user_id).toBeUndefined();
      expect(state.client.persistence.props["Org Id"]).toBeUndefined();
      const stored = JSON.parse(window.localStorage.getItem(storageKey));
      expect(stored.$user_id).toBeUndefined();
      expect(stored["Org Id"]).toBeUndefined();
      expect(document.cookie).not.toContain(`${storageKey}=`);
    },
  );

  it.each(["dirty", "already cleaned"])(
    "expires every cookie scope before SDK init (%s copies)",
    async (kind) => {
      const props = {
        ...cookieProps,
        supplied: kind === "dirty" ? dirtyUrl : "ordinary value",
      };
      for (const domain of domains) seedCookie(domain, props);
      state.cookieWrites = [];
      state.storageWrites = [];
      await load();
      expect(state.initCalls).toBe(1);
      expect(state.beforeInit.cookies).not.toContain(`${storageKey}=`);
      expect(document.cookie).not.toContain(`${storageKey}=`);
      expect(JSON.parse(state.beforeInit.persistence)).toMatchObject({
        ...props,
        supplied: props.supplied.replace(secret, "auth-secret-redacted"),
      });
      const expiredScopes = state.beforeInit.cookieWrites.map((write) => {
        const scope = write
          .split(";")
          .find((part) => part.trim().startsWith("Domain="));
        return scope ? `; ${scope.trim()}` : "";
      });
      expect(expiredScopes).toEqual(expect.arrayContaining(domains));
      expect(state.beforeInit.cookieWrites.length).toBeGreaterThanOrEqual(
        domains.length,
      );
      for (const write of state.beforeInit.cookieWrites) {
        expect(write).toContain("Max-Age=0");
        expect(write).not.toContain("Max-Age=31536000");
      }
      expect(JSON.stringify(state.beforeInit.storageWrites)).not.toContain(
        secret,
      );
    },
  );

  it("merges cleaned cookie properties once without overwriting any existing key", async () => {
    const existing = {
      distinct_id: "fake-current-distinct-id",
      $user_id: "fake-current-user-id",
      "Org Id": "fake-current-org-id",
      falseValue: false,
      nullValue: null,
      emptyValue: "",
      zeroValue: 0,
      sentinel: "None",
    };
    window.localStorage.setItem(storageKey, JSON.stringify(existing));
    seedCookie("; Domain=eu.example.invalid", {
      ...cookieProps,
      falseValue: true,
      nullValue: "old",
      emptyValue: "old",
      zeroValue: 1,
      sentinel: "old",
    });
    state.storageWrites = [];
    await load();
    expect(state.initCalls).toBe(1);
    expect(JSON.parse(state.beforeInit.persistence)).toEqual({
      ...cookieProps,
      supplied: dirtyUrl.replace(secret, "auth-secret-redacted"),
      ...existing,
    });
    expect(state.client.persistence.props).toMatchObject(existing);
    expect(state.beforeInit.cookies).not.toContain(`${storageKey}=`);
    expect(JSON.stringify(state.beforeInit.storageWrites)).not.toContain(
      secret,
    );
  });

  it.each(["throws", "does not stick"])(
    "fails closed without planting cookies when localStorage %s",
    async (failure) => {
      seedCookie("; Domain=eu.example.invalid");
      state.cookieWrites = [];
      vi.spyOn(
        Object.getPrototypeOf(window.localStorage),
        "setItem",
      ).mockImplementation(() => {
        if (failure === "throws") throw new Error("Storage unavailable");
      });
      await load();
      expect(state.initCalls).toBe(0);
      expect(state.client).toBeNull();
      expect(state.cookieWrites).toEqual([]);
    },
  );

  it("fails closed if a cookie copy cannot be expired", async () => {
    seedCookie("; Domain=eu.example.invalid");
    vi.spyOn(document, "cookie", "set").mockImplementation(() => {});
    await load();
    expect(state.initCalls).toBe(0);
    expect(state.client).toBeNull();
  });

  it("does not merge conflicting cookie identities", async () => {
    seedCookie("", { ...cookieProps, supplied: "ordinary value" });
    seedCookie("; Domain=eu.example.invalid", {
      ...cookieProps,
      $user_id: "fake-different-user-id",
      supplied: "ordinary value",
    });
    await load();
    expect(state.initCalls).toBe(0);
    expect(window.localStorage.getItem(storageKey)).toBeNull();
  });

  it("identifies Appcues while corrupt Mixpanel persistence disables init", async () => {
    window.localStorage.setItem(storageKey, "not-json");
    const { identifyUser, resetUser } = await load();
    expect(state.initCalls).toBe(0);
    identifyUser({
      id: "fake-current-user-id",
      email: "person@example.invalid",
    });
    expect(window.Appcues.identify).toHaveBeenCalledExactlyOnceWith(
      "fake-current-user-id",
      { email: "person@example.invalid" },
    );
    resetUser();
    expect(window.Appcues.reset).toHaveBeenCalledTimes(1);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  OMEGA_SWITCH,
  isOmegaConsoleEnabled,
  readOmegaConsoleSwitch,
  settleOmegaConsoleSwitch,
} from "../flags";
import {
  GATEWAY_STATE,
  askGateway,
  gatewayProbeState,
  gatewayProbeUrl,
  resetGatewayProbe,
  settleGatewayProbe,
  subscribeGatewayProbe,
} from "../gatewayProbe";

// A fetch Response as far as the probe reads one.
function answer(status, body, type = "application/json; charset=utf-8") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name) => (name.toLowerCase() === "content-type" ? type : null),
    },
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
  };
}

// services/omega-gateway/src/server.mjs: GET /healthz -> 200 {"live": true}.
const gatewayLive = () => answer(200, { live: true });
// frontend/nginx.conf `location = /omega/api/healthz`: no gateway, not a failure.
const uiNoGateway = () => answer(200, '{"live":false}');
// The JSON 404 of `location ^~ /omega/` (an install older than that location).
const uiNginx404 = () => answer(404, '{"error":"not_found"}');
// frontend/vercel.json rewrites every path to the SPA; so does Vite's dev server.
const spaFallback = () =>
  answer(200, '<!doctype html><div id="root"></div>', "text/html");

// A fetch whose answer the test gives later.
function deferredFetch() {
  let respond;
  const fetchMock = vi.fn(
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  return { fetchMock, respond: (response) => respond(response) };
}

describe("gatewayProbeUrl", () => {
  it("asks <base>/healthz on the data client's base", () => {
    expect(gatewayProbeUrl(undefined)).toBe("/omega/api/healthz");
    expect(gatewayProbeUrl("")).toBe("/omega/api/healthz");
    expect(gatewayProbeUrl("/edge/omega")).toBe("/edge/omega/healthz");
    // Only a same-origin path, as omegaClient.js resolveOmegaApiBase.
    expect(gatewayProbeUrl("https://elsewhere.example/omega/api")).toBe(
      "/omega/api/healthz",
    );
    expect(gatewayProbeUrl("/omega/api/")).toBe("/omega/api/healthz");
  });
});

describe("askGateway", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("is yes only for the gateway's own 200 JSON {live: true}", async () => {
    const fetchImpl = vi.fn(async () => gatewayLive());
    await expect(askGateway({ fetchImpl })).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe(gatewayProbeUrl());
    expect(init).toMatchObject({
      method: "GET",
      credentials: "omit",
      cache: "no-store",
    });
    // The browser's default Accept (*/*): Vite's dev server answers its SPA
    // page to that, where an Accept of application/json gets a 404.
    expect(init.headers).toBeUndefined();
  });

  it.each([
    ["the UI's 200 {live: false} (no gateway)", uiNoGateway],
    ["the UI nginx's older JSON 404 for /omega/", uiNginx404],
    ["an SPA fallback's 200 index.html", spaFallback],
    ["a 200 JSON that is not the gateway's", () => answer(200, { ok: true })],
    ["a 503 JSON", () => answer(503, { error: "omega_gateway_draining" })],
    ["a 200 JSON body that does not parse", () => answer(200, "{not json")],
  ])("is no for %s", async (_name, make) => {
    await expect(askGateway({ fetchImpl: async () => make() })).resolves.toBe(
      false,
    );
  });

  it("is no on a network error, a synchronous throw, or no fetch at all", async () => {
    await expect(
      askGateway({
        fetchImpl: async () => {
          throw new TypeError("Failed to fetch");
        },
      }),
    ).resolves.toBe(false);
    await expect(
      askGateway({
        fetchImpl: () => {
          throw new TypeError("fetch is not a function");
        },
      }),
    ).resolves.toBe(false);
    vi.stubGlobal("fetch", undefined);
    await expect(askGateway()).resolves.toBe(false);
  });

  it("is no at the timeout, and aborts the request, even if fetch ignores the abort", async () => {
    vi.useFakeTimers();
    let signal;
    const fetchImpl = vi.fn((_url, init) => {
      signal = init.signal;
      return new Promise(() => {});
    });
    let settled = false;
    const asked = askGateway({ fetchImpl, timeoutMs: 2000 });
    asked.then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(1999);
    expect(settled).toBe(false);
    expect(signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(asked).resolves.toBe(false);
    expect(signal.aborted).toBe(true);
  });
});

describe("readOmegaConsoleSwitch", () => {
  it("reads unset as auto, and the operator's on and off values as set", () => {
    expect(readOmegaConsoleSwitch({}, {})).toBe(OMEGA_SWITCH.AUTO);
    expect(readOmegaConsoleSwitch({}, { VITE_OMEGA_CONSOLE: "" })).toBe(
      OMEGA_SWITCH.AUTO,
    );
    expect(readOmegaConsoleSwitch({}, { VITE_OMEGA_CONSOLE: "0" })).toBe(
      OMEGA_SWITCH.OFF,
    );
    expect(readOmegaConsoleSwitch({}, { VITE_OMEGA_CONSOLE: " False " })).toBe(
      OMEGA_SWITCH.OFF,
    );
    expect(readOmegaConsoleSwitch({}, { VITE_OMEGA_CONSOLE: "1" })).toBe(
      OMEGA_SWITCH.ON,
    );
    expect(readOmegaConsoleSwitch({}, { VITE_OMEGA_CONSOLE: "true" })).toBe(
      OMEGA_SWITCH.ON,
    );
    // The CDN's runtime "false" (scripts/write-runtime-config.mjs) beats a
    // build-time "1".
    expect(
      readOmegaConsoleSwitch(
        { VITE_OMEGA_CONSOLE: "false" },
        { VITE_OMEGA_CONSOLE: "1" },
      ),
    ).toBe(OMEGA_SWITCH.OFF);
  });
});

describe("the switch with VITE_OMEGA_CONSOLE unset", () => {
  beforeEach(() => {
    resetGatewayProbe();
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    vi.stubEnv("VITE_OMEGA_API_BASE", "");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    delete window.__FUTURE_AGI_CONFIG__;
    resetGatewayProbe();
  });

  it("is checking from the moment it is asked, and tells subscribers when it answers", async () => {
    const { fetchMock, respond } = deferredFetch();
    vi.stubGlobal("fetch", fetchMock);
    const seen = [];
    const unsubscribe = subscribeGatewayProbe(() =>
      seen.push(gatewayProbeState()),
    );
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.IDLE);
    const settled = settleOmegaConsoleSwitch();
    // main.jsx renders right after this call: the first render sees CHECKING.
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.CHECKING);
    expect(isOmegaConsoleEnabled()).toBe(false);
    respond(gatewayLive());
    await settled;
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.UP);
    expect(isOmegaConsoleEnabled()).toBe(true);
    expect(seen).toEqual([GATEWAY_STATE.CHECKING, GATEWAY_STATE.UP]);
    unsubscribe();
  });

  it("ignores an answer that lands after a reset", async () => {
    const { fetchMock, respond } = deferredFetch();
    vi.stubGlobal("fetch", fetchMock);
    const stale = settleOmegaConsoleSwitch();
    resetGatewayProbe();
    respond(gatewayLive());
    await stale;
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.IDLE);
    expect(isOmegaConsoleEnabled()).toBe(false);
  });

  it("asks once per page, however often it is asked", async () => {
    const fetchMock = vi.fn(async () => gatewayLive());
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    await settleOmegaConsoleSwitch();
    await settleGatewayProbe();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/omega/api/healthz");
  });

  it("stays off where the UI answers 'no gateway'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => uiNoGateway()),
    );
    await settleOmegaConsoleSwitch();
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.DOWN);
    expect(isOmegaConsoleEnabled()).toBe(false);
  });

  it("settles off with no fetch at all", async () => {
    vi.stubGlobal("fetch", undefined);
    await expect(settleOmegaConsoleSwitch()).resolves.toBeUndefined();
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.DOWN);
    expect(isOmegaConsoleEnabled()).toBe(false);
  });

  it.each([["0"], ["false"]])(
    "asks nothing and stays off with %s",
    async (value) => {
      vi.stubEnv("VITE_OMEGA_CONSOLE", value);
      const fetchMock = vi.fn(async () => gatewayLive());
      vi.stubGlobal("fetch", fetchMock);
      await settleOmegaConsoleSwitch();
      expect(fetchMock).not.toHaveBeenCalled();
      expect(gatewayProbeState()).toBe(GATEWAY_STATE.IDLE);
      expect(isOmegaConsoleEnabled()).toBe(false);
    },
  );

  it("asks nothing and is on with 1, as before", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    const fetchMock = vi.fn(async () => uiNoGateway());
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(gatewayProbeState()).toBe(GATEWAY_STATE.IDLE);
    expect(isOmegaConsoleEnabled()).toBe(true);
  });

  it("follows the runtime config's 0 over the probe", async () => {
    window.__FUTURE_AGI_CONFIG__ = { VITE_OMEGA_CONSOLE: "0" };
    const fetchMock = vi.fn(async () => gatewayLive());
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(isOmegaConsoleEnabled()).toBe(false);
  });
});

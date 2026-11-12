import { describe, expect, it, vi } from "vitest";

import {
  DIAGNOSIS,
  diagnoseMissingHello,
  diagnosisCopy,
  isSafari,
} from "../diagnosis";

const SRC = "http://127.0.0.1:4190/?embed=1#/runs";
const CHROME_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36";
const SAFARI_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15";

const nav = ({ ua = CHROME_UA, denied = [], brave = false } = {}) => ({
  userAgent: ua,
  permissions: {
    query: vi.fn(async ({ name }) => {
      if (!["loopback-network", "local-network-access"].includes(name)) {
        throw new TypeError("unknown permission");
      }
      return { state: denied.includes(name) ? "denied" : "prompt" };
    }),
  },
  ...(brave ? { brave: { isBrave: async () => true } } : {}),
});

const up = () => vi.fn(async () => ({ type: "opaque" }));
const down = () =>
  vi.fn(async () => Promise.reject(new TypeError("Failed to fetch")));

describe("diagnoseMissingHello", () => {
  it("names a denied local-network permission first", async () => {
    for (const name of ["loopback-network", "local-network-access"]) {
      const fetchImpl = up();
      const result = await diagnoseMissingHello({
        src: SRC,
        probePath: "/studio/v1/openapi.json",
        nav: nav({ denied: [name] }),
        fetchImpl,
      });
      expect(result).toEqual({
        cause: DIAGNOSIS.localNetworkDenied,
        origin: "http://127.0.0.1:4190",
      });
      expect(fetchImpl).not.toHaveBeenCalled();
    }
  });

  it("names Safari when an https page frames an http server", async () => {
    const result = await diagnoseMissingHello({
      src: SRC,
      probePath: "/studio/v1/openapi.json",
      nav: nav({ ua: SAFARI_UA }),
      fetchImpl: up(),
      parentProtocol: "https:",
    });
    expect(result.cause).toBe(DIAGNOSIS.safari);
    expect(isSafari({ userAgent: CHROME_UA })).toBe(false);
  });

  it("does not blame Safari on a plain-http parent", async () => {
    const result = await diagnoseMissingHello({
      src: SRC,
      probePath: "/studio/v1/openapi.json",
      nav: nav({ ua: SAFARI_UA }),
      fetchImpl: up(),
      parentProtocol: "http:",
    });
    expect(result.cause).toBe(DIAGNOSIS.framingBlocked);
  });

  it("says framing is blocked when the server answers the probe", async () => {
    const fetchImpl = up();
    const result = await diagnoseMissingHello({
      src: SRC,
      probePath: "/studio/v1/openapi.json",
      nav: nav(),
      fetchImpl,
    });
    expect(result.cause).toBe(DIAGNOSIS.framingBlocked);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:4190/studio/v1/openapi.json");
    expect(init).toMatchObject({
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  });

  it("says nothing is running when the probe fails, or Brave when it is Brave", async () => {
    const args = { src: SRC, probePath: "/studio/v1/openapi.json" };
    expect(
      (await diagnoseMissingHello({ ...args, nav: nav(), fetchImpl: down() }))
        .cause,
    ).toBe(DIAGNOSIS.notRunning);
    expect(
      (
        await diagnoseMissingHello({
          ...args,
          nav: nav({ brave: true }),
          fetchImpl: down(),
        })
      ).cause,
    ).toBe(DIAGNOSIS.brave);
  });

  it("names a pending local-network permission when the probe gets no answer in time", async () => {
    // Chrome holds a request to this computer while its permission prompt is
    // open or was dismissed: the probe neither answers nor fails.
    vi.useFakeTimers();
    try {
      const hung = vi.fn(
        (_url, { signal }) =>
          new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      );
      const pending = diagnoseMissingHello({
        src: SRC,
        probePath: "/studio/v1/openapi.json",
        nav: nav(),
        fetchImpl: hung,
      });
      await vi.advanceTimersByTimeAsync(3100);
      expect((await pending).cause).toBe(DIAGNOSIS.localNetworkPrompt);
      const granted = {
        userAgent: CHROME_UA,
        permissions: { query: vi.fn(async () => ({ state: "granted" })) },
      };
      const stillDown = diagnoseMissingHello({
        src: SRC,
        probePath: "/studio/v1/openapi.json",
        nav: granted,
        fetchImpl: hung,
      });
      await vi.advanceTimersByTimeAsync(3100);
      expect((await stillDown).cause).toBe(DIAGNOSIS.notRunning);
      // Brave Shields keeps its own words, whatever the permission says.
      const shields = diagnoseMissingHello({
        src: SRC,
        probePath: "/studio/v1/openapi.json",
        nav: nav({ brave: true }),
        fetchImpl: hung,
      });
      await vi.advanceTimersByTimeAsync(3100);
      expect((await shields).cause).toBe(DIAGNOSIS.brave);
    } finally {
      vi.useRealTimers();
    }
  });

  it("makes no request without a probe path", async () => {
    const fetchImpl = up();
    const result = await diagnoseMissingHello({
      src: "/omega/console/?embed=1",
      nav: nav(),
      fetchImpl,
    });
    expect(result.cause).toBe(DIAGNOSIS.noResponse);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("has plain copy for every cause", () => {
    Object.values(DIAGNOSIS).forEach((cause) => {
      const { title, body } = diagnosisCopy(cause, {
        label: "local Studio",
        origin: "http://127.0.0.1:4190",
      });
      expect(title.length).toBeGreaterThan(5);
      expect(body.length).toBeGreaterThan(10);
    });
    expect(
      diagnosisCopy(DIAGNOSIS.notRunning, { origin: "http://127.0.0.1:4190" })
        .title,
    ).toContain("127.0.0.1:4190");
    expect(
      diagnosisCopy(DIAGNOSIS.localNetworkPrompt, { label: "local Studio" }),
    ).toEqual({
      title: "Your browser is asking to reach apps on this device",
      body: expect.stringContaining("allow it"),
    });
  });
});

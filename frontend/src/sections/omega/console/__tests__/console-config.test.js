import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CONSOLE_BASE_PATH,
  CONSOLE_FRAME_ALLOW,
  CONSOLE_FRAME_SANDBOX,
  CONSOLE_ROUTE_MODE,
  DEFAULT_CONSOLE_SRC,
  consoleFrameAttributes,
  consoleOrigin,
  consoleSrc,
  resolveConsoleSrc,
} from "../console-config";

const ORIGIN = "https://app.futureagi.com";

describe("console config", () => {
  it("frames the EE Console in embed mode on this site, with hash routes", () => {
    expect(DEFAULT_CONSOLE_SRC).toBe("/omega/console/?embed=1");
    expect(CONSOLE_BASE_PATH).toBe("/dashboard/console");
    expect(CONSOLE_ROUTE_MODE).toBe("hash");
    expect(resolveConsoleSrc(undefined, ORIGIN)).toBe(DEFAULT_CONSOLE_SRC);
    expect(resolveConsoleSrc("  ", ORIGIN)).toBe(DEFAULT_CONSOLE_SRC);
  });

  it("accepts a same-site path or an http(s) URL", () => {
    expect(resolveConsoleSrc("/omega/console/?embed=1", ORIGIN)).toBe(
      "/omega/console/?embed=1",
    );
    expect(
      resolveConsoleSrc("http://127.0.0.1:5174/omega/console/?embed=1", ORIGIN),
    ).toBe("http://127.0.0.1:5174/omega/console/?embed=1");
  });

  it("falls back to the default for anything else", () => {
    expect(
      resolveConsoleSrc(["java", "script:alert(1)"].join(""), ORIGIN),
    ).toBe(DEFAULT_CONSOLE_SRC);
    expect(resolveConsoleSrc("data:text/html,hi", ORIGIN)).toBe(
      DEFAULT_CONSOLE_SRC,
    );
    expect(resolveConsoleSrc("http://[bad", ORIGIN)).toBe(DEFAULT_CONSOLE_SRC);
  });
});

describe("the Console on its own origin (CP P6)", () => {
  afterEach(() => {
    delete window.__FUTURE_AGI_CONFIG__;
    vi.unstubAllEnvs();
  });

  it("takes the runtime config's URL over the build's", () => {
    vi.stubEnv(
      "VITE_OMEGA_CONSOLE_URL",
      "https://build.example/omega/console/?embed=1",
    );
    expect(consoleSrc()).toBe("https://build.example/omega/console/?embed=1");
    window.__FUTURE_AGI_CONFIG__ = {
      VITE_OMEGA_CONSOLE_URL:
        "https://console.omega.futureagi.com/omega/console/?embed=1",
    };
    expect(consoleSrc()).toBe(
      "https://console.omega.futureagi.com/omega/console/?embed=1",
    );
    window.__FUTURE_AGI_CONFIG__ = { VITE_OMEGA_CONSOLE_URL: "  " };
    expect(consoleSrc()).toBe("https://build.example/omega/console/?embed=1");
  });

  it("refuses a URL that carries credentials", () => {
    expect(
      resolveConsoleSrc("https://user:pass@console.example/?embed=1", ORIGIN),
    ).toBe(DEFAULT_CONSOLE_SRC);
  });

  it("keeps today's frame on this site, and sandboxes another origin", () => {
    expect(consoleFrameAttributes("/omega/console/?embed=1", ORIGIN)).toEqual({
      allow: CONSOLE_FRAME_ALLOW,
      sandbox: undefined,
      crossOrigin: false,
    });
    const hosted = consoleFrameAttributes(
      "https://console.omega.futureagi.com/omega/console/?embed=1",
      ORIGIN,
    );
    expect(hosted).toEqual({
      allow: "clipboard-write",
      sandbox: CONSOLE_FRAME_SANDBOX,
      crossOrigin: true,
    });
    // Never top navigation, and the frame keeps its own origin.
    expect(CONSOLE_FRAME_SANDBOX.split(" ")).toEqual(
      expect.arrayContaining(["allow-scripts", "allow-same-origin"]),
    );
    expect(CONSOLE_FRAME_SANDBOX).not.toMatch(/allow-top-navigation/);
    const local = consoleFrameAttributes(
      "http://127.0.0.1:43211/omega/console/?embed=1",
      "http://127.0.0.1:43210",
    );
    expect(local.allow).toBe(
      "clipboard-write; local-network-access; loopback-network",
    );
    expect(consoleOrigin("/omega/console/", ORIGIN)).toBe(ORIGIN);
  });
});

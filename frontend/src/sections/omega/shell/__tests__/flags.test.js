import { afterEach, describe, expect, it, vi } from "vitest";

import { isOmegaConsoleEnabled, readOmegaConsoleFlag } from "../flags";

describe("readOmegaConsoleFlag", () => {
  it("is off unless the value is 1 or true", () => {
    expect(readOmegaConsoleFlag({}, {})).toBe(false);
    expect(readOmegaConsoleFlag({}, { VITE_OMEGA_CONSOLE: "" })).toBe(false);
    expect(readOmegaConsoleFlag({}, { VITE_OMEGA_CONSOLE: "0" })).toBe(false);
    expect(readOmegaConsoleFlag({}, { VITE_OMEGA_CONSOLE: "yes" })).toBe(false);
    expect(readOmegaConsoleFlag({}, { VITE_OMEGA_CONSOLE: "1" })).toBe(true);
    expect(readOmegaConsoleFlag({}, { VITE_OMEGA_CONSOLE: " TRUE " })).toBe(
      true,
    );
  });

  it("lets runtime config override the build-time value", () => {
    expect(
      readOmegaConsoleFlag(
        { VITE_OMEGA_CONSOLE: "0" },
        { VITE_OMEGA_CONSOLE: "1" },
      ),
    ).toBe(false);
    expect(
      readOmegaConsoleFlag(
        { VITE_OMEGA_CONSOLE: "1" },
        { VITE_OMEGA_CONSOLE: "0" },
      ),
    ).toBe(true);
    // An empty runtime value falls through to the build value.
    expect(
      readOmegaConsoleFlag(
        { VITE_OMEGA_CONSOLE: "" },
        { VITE_OMEGA_CONSOLE: "1" },
      ),
    ).toBe(true);
  });
});

describe("isOmegaConsoleEnabled", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    delete window.__FUTURE_AGI_CONFIG__;
  });

  it("reads VITE_OMEGA_CONSOLE from the build env", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    expect(isOmegaConsoleEnabled()).toBe(false);
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    expect(isOmegaConsoleEnabled()).toBe(true);
  });

  it("reads the runtime config written by the container entrypoint", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    window.__FUTURE_AGI_CONFIG__ = { VITE_OMEGA_CONSOLE: "1" };
    expect(isOmegaConsoleEnabled()).toBe(true);
  });
});

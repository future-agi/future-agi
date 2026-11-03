import { describe, it, expect, afterEach, vi } from "vitest";
import { isScenarioSampleMode } from "../scenariosSampleMode";

describe("isScenarioSampleMode — dev-only gate", () => {
  const setSearch = (search) => {
    Object.defineProperty(window, "location", {
      value: { search },
      writable: true,
    });
  };

  afterEach(() => {
    vi.unstubAllEnvs();
    setSearch("");
  });

  it("is on with ?scnSample in a dev build", () => {
    vi.stubEnv("DEV", true);
    setSearch("?scnSample=1");
    expect(isScenarioSampleMode()).toBe(true);
  });

  it("is off with ?scnSample in a production build", () => {
    // In prod the switch must do nothing — otherwise amend "succeeds" against
    // fixtures for a real user.
    vi.stubEnv("DEV", false);
    setSearch("?scnSample=1");
    expect(isScenarioSampleMode()).toBe(false);
  });

  it("is off without the flag", () => {
    vi.stubEnv("DEV", true);
    setSearch("");
    expect(isScenarioSampleMode()).toBe(false);
  });
});

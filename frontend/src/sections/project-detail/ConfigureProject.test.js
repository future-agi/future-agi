import { describe, expect, it } from "vitest";

import { getSamplingRatePercent } from "./ConfigureProject";

describe("getSamplingRatePercent", () => {
  it("converts a numeric sampling_rate fraction to a percent", () => {
    expect(getSamplingRatePercent({ sampling_rate: 0.35 }, "observe")).toBe(35);
  });

  it("returns 0 for a genuinely-configured 0 sampling_rate", () => {
    expect(getSamplingRatePercent({ sampling_rate: 0 }, "observe")).toBe(0);
  });

  it("falls back while the project hasn't loaded yet (projectDetail nullish)", () => {
    expect(getSamplingRatePercent(undefined, "observe")).toBe(0);
    expect(getSamplingRatePercent(null, "prototype")).toBeUndefined();
  });
});

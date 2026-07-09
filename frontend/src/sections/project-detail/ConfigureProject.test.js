import { describe, expect, it, vi, beforeEach } from "vitest";

const loggerErrorMock = vi.hoisted(() => vi.fn());
vi.mock("src/utils/logger", () => ({
  default: { error: loggerErrorMock },
}));

import { getSamplingRatePercent } from "./ConfigureProject";

describe("getSamplingRatePercent", () => {
  beforeEach(() => {
    loggerErrorMock.mockReset();
  });

  it("converts a numeric sampling_rate fraction to a percent", () => {
    expect(getSamplingRatePercent({ sampling_rate: 0.35 }, "observe")).toBe(35);
    expect(loggerErrorMock).not.toHaveBeenCalled();
  });

  it("returns 0 for a genuinely-configured 0 sampling_rate without logging", () => {
    expect(getSamplingRatePercent({ sampling_rate: 0 }, "observe")).toBe(0);
    expect(loggerErrorMock).not.toHaveBeenCalled();
  });

  it("does not log while the project hasn't loaded yet (projectDetail undefined)", () => {
    expect(getSamplingRatePercent(undefined, "observe")).toBe(0);
    expect(getSamplingRatePercent(null, "prototype")).toBeUndefined();
    expect(loggerErrorMock).not.toHaveBeenCalled();
  });

  it("falls back to 0 for observe AND logs when sampling_rate is a non-numeric anomaly", () => {
    const projectDetail = { id: "p1", sampling_rate: "not-a-number" };

    expect(getSamplingRatePercent(projectDetail, "observe")).toBe(0);

    expect(loggerErrorMock).toHaveBeenCalledTimes(1);
    const [message, error, context] = loggerErrorMock.mock.calls[0];
    expect(message).toMatch(/sampling_rate is not a number/i);
    expect(error).toBeNull();
    expect(context).toEqual({
      sampling_rate: "not-a-number",
      module: "observe",
    });
  });

  it("falls back to undefined for prototype AND still logs the anomaly", () => {
    const projectDetail = { id: "p1", sampling_rate: null };

    expect(getSamplingRatePercent(projectDetail, "prototype")).toBeUndefined();
    expect(loggerErrorMock).toHaveBeenCalledTimes(1);
    expect(loggerErrorMock.mock.calls[0][2]).toEqual({
      sampling_rate: null,
      module: "prototype",
    });
  });

  it("logs when sampling_rate is missing entirely from a loaded project", () => {
    const projectDetail = { id: "p1", name: "Test Project" };

    expect(getSamplingRatePercent(projectDetail, "observe")).toBe(0);
    expect(loggerErrorMock).toHaveBeenCalledTimes(1);
  });
});

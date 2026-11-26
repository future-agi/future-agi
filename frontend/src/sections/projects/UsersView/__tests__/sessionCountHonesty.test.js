import { describe, expect, it } from "vitest";
import {
  formatUserSessionCount,
  formatUserSessionCountTooltip,
} from "../sessionCountHonesty";

describe("Observe Users session-count honesty", () => {
  it("marks only approximate session counts", () => {
    expect(
      formatUserSessionCount({
        value: 1234,
        data: { num_sessions_is_approximate: true },
      }),
    ).toBe("~1,234");
    expect(
      formatUserSessionCount({
        value: 1234,
        data: { num_sessions_is_approximate: false },
      }),
    ).toBe("1,234");
  });

  it("explains the approximation in the Sessions column tooltip", () => {
    expect(
      formatUserSessionCountTooltip({
        data: { num_sessions_is_approximate: true },
      }),
    ).toContain("Approximate session count");
    expect(
      formatUserSessionCountTooltip({
        data: { num_sessions_is_approximate: false },
      }),
    ).toBeNull();
  });
});

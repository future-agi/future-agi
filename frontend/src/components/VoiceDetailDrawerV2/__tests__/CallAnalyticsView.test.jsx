import { describe, expect, it } from "vitest";
import { fmtWpm } from "../formatters";

describe("fmtWpm", () => {
  it("rounds precise WPM metrics for display", () => {
    expect(fmtWpm(259.30878424)).toBe("259");
    expect(fmtWpm(176.81041613388277)).toBe("177");
  });

  it("preserves the empty metric placeholder", () => {
    expect(fmtWpm(null)).toBe("—");
  });
});

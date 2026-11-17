// W6 A2: the Try names why it stopped from the try itself.
import { describe, expect, it } from "vitest";
import { tryStopLine } from "../tryStopLine";

describe("tryStopLine", () => {
  it("reads the try", () => {
    expect(tryStopLine(null)).toBeNull();
    expect(tryStopLine({ trace_id: "t" })).toBe("Stop reason: final answer");
    expect(
      tryStopLine({
        trace_id: "t",
        error: "Stopped at 600 tokens",
        trace_summary: { status: "stopped" },
      }),
    ).toBe("Stopped at 600 tokens");
    expect(
      tryStopLine({
        trace_summary: { status: "error", error: { step: 3, text: "boom" } },
      }),
    ).toBe("Stopped at step 3: boom");
    expect(tryStopLine({})).toBeNull();
  });
});

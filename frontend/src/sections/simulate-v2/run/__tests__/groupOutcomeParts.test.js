import { describe, it, expect } from "vitest";
import { groupOutcomeParts } from "../traceTableHelpers";

describe("groupOutcomeParts", () => {
  it("counts scenarios and leaves zeros out", () => {
    const parts = groupOutcomeParts({
      rows: [{ samples: ["passed", "failed"] }, { samples: ["failed"] }],
      outcomes: { passed: 0, mixed: 2, failing: 1 },
    });
    expect(parts.map((p) => p.text)).toEqual(["2 flaky", "1 failed"]);
  });
  it("by goal, a one-scenario group still counts scenarios", () => {
    const parts = groupOutcomeParts({
      rows: [{ status: "flaky", samples: ["passed", "failed", "failed"] }],
      outcomes: { mixed: 1 },
    });
    expect(parts.map((p) => p.text)).toEqual(["1 flaky"]);
  });
  it("by actor, a one-scenario group reads as that scenario's trials", () => {
    const parts = groupOutcomeParts({
      trialsWhenOne: true,
      rows: [{ status: "flaky", samples: ["passed", "failed", "failed"] }],
      outcomes: { mixed: 1 },
    });
    expect(parts).toHaveLength(1);
    expect(parts[0].text).toBe("1 of 3 trials passed");
    expect(parts[0].dot).toBe("status.warning.solid");
  });
  it("says Not scored for a one-scenario group that wasn't scored", () => {
    const parts = groupOutcomeParts({
      rows: [{ status: "unmeasured", samples: ["unmeasured", "unmeasured"] }],
      outcomes: { unmeasured: 1 },
    });
    expect(parts.map((p) => p.text)).toEqual(["Not scored"]);
    expect(parts[0].dot).toBe("text.disabled");
  });
  it("counts not-scored scenarios in a multi-scenario group", () => {
    const parts = groupOutcomeParts({
      rows: [{ status: "unmeasured" }, { status: "error" }],
      outcomes: { unmeasured: 2 },
    });
    expect(parts.map((p) => p.text)).toEqual(["2 not scored"]);
  });
});

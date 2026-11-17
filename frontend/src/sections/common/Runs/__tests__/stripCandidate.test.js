import { describe, expect, it } from "vitest";
import { stripCandidate } from "../configDiff";

const runIds = ["A", "B", "C", "D", "E"];

describe("stripCandidate", () => {
  it("cand= wins when it is a run in view and not the baseline", () => {
    expect(
      stripCandidate({ cand: "D", focused: "C", baselineId: "B", runIds }),
    ).toBe("D");
  });
  it("follows the focused run when there is no cand", () => {
    expect(stripCandidate({ focused: "C", baselineId: "B", runIds })).toBe("C");
    expect(
      stripCandidate({ cand: "Z", focused: "E", baselineId: "B", runIds }),
    ).toBe("E");
  });
  it("falls back to the first non-baseline run", () => {
    expect(stripCandidate({ focused: "B", baselineId: "B", runIds })).toBe("A");
    expect(stripCandidate({ baselineId: "A", runIds })).toBe("B");
  });
  it("no baseline means no pair", () => {
    expect(
      stripCandidate({ cand: "A", baselineId: "none", runIds }),
    ).toBeNull();
    expect(stripCandidate({ baselineId: null, runIds })).toBeNull();
  });
});

import { describe, it, expect } from "vitest";
import { quickChips } from "../quickChips";

const labels = (status) => quickChips(status).map((c) => c.label);

describe("quickChips", () => {
  it("offers the example agent until one is being tested", () => {
    expect(labels({ have: {} })).toContain("test the drive_thru example");
    expect(labels({ agent: "drive_thru", stage: "understand", have: {} })).not.toContain(
      "test the drive_thru example"
    );
  });

  it("suggests scenarios once a world exists but none are written", () => {
    expect(labels({ agent: "a", stage: "build", have: { world: true } })).toContain(
      "write 5 hard scenarios"
    );
  });

  it("counts the scenarios it offers to run", () => {
    expect(labels({ agent: "a", stage: "scenarios", have: { world: true, scenarios: 4 } })).toContain(
      "run all 4 against the world"
    );
  });

  it("switches to per-run suggestions once on the run stage", () => {
    const got = labels({ agent: "a", stage: "run", have: { scenarios: 4 } });
    expect(got).toContain("what can be run?");
    expect(got).toContain("run one live call");
    expect(got).not.toContain("run all 4 against the world");
  });

  it("advances the stage by sending nothing at all", () => {
    const next = quickChips({ agent: "a", stage: "understand", have: {} }).find(
      (c) => c.label === "next stage →"
    );
    expect(next).toEqual({ label: "next stage →", say: "" });
  });

  it("cannot advance before an agent is known", () => {
    expect(labels({ stage: "reception", have: {} })).not.toContain("next stage →");
  });
});

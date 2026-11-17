import { describe, expect, it } from "vitest";
import { canonicalRunIds } from "../useWorkspaceData";

const LINK = ["r1a", "r1b", "r1c", "r1d", "r1e", "r2a", "r2b"];
const SEL = [...LINK, "r2c", "r2d", "r2e", "r2f", "s02"];

describe("canonicalRunIds (TC-02)", () => {
  it("is the same before the pair is picked and after (one request)", () => {
    const before = canonicalRunIds([], SEL, "r2b", LINK);
    const after = canonicalRunIds(["r2b", "r1d"], SEL, "r2b", LINK);
    expect(after).toEqual(before);
    expect(before.slice(0, 7)).toEqual([
      "r2b",
      "r1a",
      "r1b",
      "r1c",
      "r1d",
      "r1e",
      "r2a",
    ]);
  });
  it("keeps the link's runs and the lanes ahead of the rest", () => {
    const out = canonicalRunIds(["z9"], ["a1", "a2", "z9", "z8"], null, ["z8"]);
    expect(out).toEqual(["z8", "z9", "a1", "a2"]);
  });
  it("drops repeats and empty ids", () => {
    expect(canonicalRunIds(["b", "b", null], ["a", "", "a"], "x")).toEqual([
      "b",
      "a",
    ]);
  });
});

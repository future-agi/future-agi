import { describe, expect, it } from "vitest";

import { getEvalTargetGlyph } from "../evalGlyph";

describe("getEvalTargetGlyph", () => {
  it("uses only the recorded target type", () => {
    expect(getEvalTargetGlyph("span")).toBe("S");
    expect(getEvalTargetGlyph("trace")).toBe("T");
    expect(getEvalTargetGlyph("session")).toBeNull();
    expect(getEvalTargetGlyph()).toBeNull();
  });
});

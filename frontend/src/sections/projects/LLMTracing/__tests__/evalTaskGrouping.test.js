import { describe, it, expect } from "vitest";
import { getGlyphMeta } from "../evalTaskGrouping";

describe("getGlyphMeta — target_type glyph", () => {
  it("returns the S glyph for span target types (singular + plural)", () => {
    expect(getGlyphMeta("span")?.code).toBe("S");
    expect(getGlyphMeta("spans")?.code).toBe("S");
  });
  it("returns the T glyph for trace target types (singular + plural)", () => {
    expect(getGlyphMeta("trace")?.code).toBe("T");
    expect(getGlyphMeta("traces")?.code).toBe("T");
  });
  it("is case-insensitive", () => {
    expect(getGlyphMeta("SPAN")?.code).toBe("S");
  });
  it("returns null for unknown / missing target types", () => {
    expect(getGlyphMeta("session")).toBeNull();
    expect(getGlyphMeta(null)).toBeNull();
    expect(getGlyphMeta(undefined)).toBeNull();
  });
});

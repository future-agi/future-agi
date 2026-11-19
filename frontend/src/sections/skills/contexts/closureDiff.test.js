import { describe, expect, it } from "vitest";
import { lineDiff } from "./closureDiff";

describe("lineDiff", () => {
  it("returns every removed and added line in order", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc\nd")).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "B" },
      { kind: "same", text: "c" },
      { kind: "added", text: "d" },
    ]);
  });

  it("treats a missing side as an added or removed file", () => {
    expect(lineDiff(null, "x")).toEqual([{ kind: "added", text: "x" }]);
    expect(lineDiff("x", null)).toEqual([{ kind: "removed", text: "x" }]);
  });

  it("refuses an oversized file instead of truncating the review", () => {
    const big = Array.from({ length: 2001 }, (_, i) => String(i)).join("\n");
    expect(() => lineDiff(big, "")).toThrow(/too large/);
  });
});

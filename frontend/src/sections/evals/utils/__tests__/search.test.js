import { describe, expect, it } from "vitest";

import { normalizeEvalSearchText } from "../search";

describe("normalizeEvalSearchText", () => {
  it("normalizes human-readable eval names", () => {
    expect(normalizeEvalSearchText(" Context   Adherence ")).toBe(
      "context_adherence",
    );
  });

  it("preserves underscore searches", () => {
    expect(normalizeEvalSearchText("context_adherence")).toBe(
      "context_adherence",
    );
  });

  it("keeps empty search empty", () => {
    expect(normalizeEvalSearchText("")).toBe("");
  });
});

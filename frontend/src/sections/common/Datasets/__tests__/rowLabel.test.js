import { describe, expect, it } from "vitest";
import { rowLabel } from "../datasetKind";

describe("rowLabel", () => {
  it("names a row the same on every screen", () => {
    expect(rowLabel("task_suite", 4)).toBe("T4");
    expect(rowLabel("rows", 57)).toBe("Row 57");
    expect(rowLabel("conversations", 3)).toBe("Row 3");
    expect(rowLabel(undefined, 12)).toBe("Row 12");
  });
  it("never prints undefined or NaN", () => {
    expect(rowLabel("task_suite", undefined)).toBe("Row");
    expect(rowLabel("rows", null)).toBe("Row");
    expect(rowLabel("rows", "")).toBe("Row");
    expect(rowLabel("rows", "abc")).toBe("Row");
  });
});

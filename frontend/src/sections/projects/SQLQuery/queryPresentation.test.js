import { describe, expect, it } from "vitest";
import {
  describeQueryWindow,
  getEditorHeight,
  isPreviousQuery,
  SQL_EDITOR_LINE_HEIGHT,
  SQL_EDITOR_PADDING,
} from "./queryPresentation";

describe("SQL editor height", () => {
  it("is monotonic, clamped and fits short queries at the unchanged line height", () => {
    const heights = Array.from({ length: 1001 }, (_, lines) =>
      getEditorHeight(lines),
    );
    expect(heights[0]).toBe(116);
    expect(heights[4]).toBe(
      4 * SQL_EDITOR_LINE_HEIGHT + 2 * SQL_EDITOR_PADDING,
    );
    expect(heights[8]).toBe(208);
    expect(heights[14]).toBe(346);
    for (let i = 1; i < heights.length; i += 1) {
      expect(heights[i]).toBeGreaterThanOrEqual(heights[i - 1]);
      expect(heights[i]).toBeGreaterThanOrEqual(116);
      expect(heights[i]).toBeLessThanOrEqual(346);
    }
  });
  it("returns stable primitive sizes without DOM measurement or feedback state", () => {
    // This pure helper runs in Node as well as jsdom. Repeated inputs cannot
    // schedule a measurement/update loop; automaticLayout handles the resize.
    const order = [4, 100, 8, 1, 14, 4, 8, 100];
    expect(order.map(getEditorHeight)).toEqual([
      116, 346, 208, 116, 346, 116, 208, 346,
    ]);
    expect(getEditorHeight(100)).toBe(getEditorHeight(1000));
  });
});

describe("SQL execution provenance", () => {
  const submitted = Object.freeze({
    sql: "SELECT count() FROM spans",
    projectId: "project-a",
    hours: 24,
  });
  it.each([
    ["sql", "SELECT count() FROM traces"],
    ["sql", "SELECT count() FROM spans "],
    ["projectId", "project-b"],
    ["hours", 1],
    ["hours", 168],
  ])(
    "marks a changed %s stale and becomes fresh when restored",
    (key, value) => {
      expect(isPreviousQuery(submitted, { ...submitted, [key]: value })).toBe(
        true,
      );
      expect(isPreviousQuery(submitted, { ...submitted })).toBe(false);
    },
  );
  it("does not label idle controls as previous results", () => {
    expect(isPreviousQuery(null, submitted)).toBe(false);
  });
  it.each([
    [1, "last 1 hour"],
    [24, "last 1 day"],
    [168, "last 7 days"],
    [744, "last 31 days"],
  ])("names the submitted %s-hour window", (hours, expected) =>
    expect(describeQueryWindow(hours)).toBe(expected),
  );
});

import { describe, expect, it } from "vitest";

import { generateNMarks } from "./common";

describe("generateNMarks", () => {
  it("places a mark on every step value for a 1-10 step 1 slider", () => {
    const values = generateNMarks(1, 10, 1).map((mark) => mark.value);
    expect(values).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("places a mark on every step value for a 0-10 step 0.5 slider", () => {
    const values = generateNMarks(0, 10, 0.5).map((mark) => mark.value);
    expect(values[0]).toBe(0);
    expect(values[values.length - 1]).toBe(10);
    expect(values).toHaveLength(21);
  });

  it("defaults to ten intervals when no step is provided", () => {
    const values = generateNMarks(0, 1).map((mark) => mark.value);
    expect(values).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]);
  });
});

import { describe, expect, it } from "vitest";
import { generateNMarks } from "../common";

const values = (marks) => marks.map((mark) => mark.value);

describe("generateNMarks", () => {
  it("places one mark on every stop of a 1-10, step 1 slider", () => {
    expect(values(generateNMarks(1, 10, 1))).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ]);
  });

  it("starts at the real min and follows a 0.5 step on a 0-10 slider", () => {
    const expected = Array.from({ length: 21 }, (_, i) => i * 0.5);
    expect(values(generateNMarks(0, 10, 0.5))).toEqual(expected);
  });

  it("starts at 0 for a 0-10, step 1 slider", () => {
    expect(values(generateNMarks(0, 10, 1))).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ]);
  });

  it("never places a mark past max when the range is not a multiple of step", () => {
    expect(values(generateNMarks(0, 1, 0.4))).toEqual([0, 0.4, 0.8]);
  });

  it("keeps the 11 evenly spaced marks when no step is passed", () => {
    expect(values(generateNMarks(0, 1))).toEqual([
      0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1,
    ]);
    expect(values(generateNMarks(0, 2))).toEqual([
      0, 0.2, 0.4, 0.6, 0.8, 1, 1.2, 1.4, 1.6, 1.8, 2,
    ]);

    const maxTokens = values(generateNMarks(1, 20000));
    expect(maxTokens).toHaveLength(11);
    expect(maxTokens[0]).toBe(1);
    expect(maxTokens[10]).toBe(20000);
  });

  it("returns marks with an empty label", () => {
    generateNMarks(1, 10, 1).forEach((mark) => {
      expect(mark.label).toBe("");
    });
  });
});

import { describe, expect, it } from "vitest";
import { scrollEdges } from "../useScrollEdges";

describe("scrollEdges", () => {
  it("reports content past the right edge at the start", () => {
    expect(
      scrollEdges({ scrollLeft: 0, scrollWidth: 1680, clientWidth: 718 }),
    ).toEqual({
      left: false,
      right: true,
    });
  });
  it("reports both edges mid-way and only the left at the end", () => {
    expect(
      scrollEdges({ scrollLeft: 400, scrollWidth: 1680, clientWidth: 718 }),
    ).toEqual({
      left: true,
      right: true,
    });
    expect(
      scrollEdges({ scrollLeft: 962, scrollWidth: 1680, clientWidth: 718 }),
    ).toEqual({
      left: true,
      right: false,
    });
  });
  it("reports nothing when everything fits", () => {
    expect(
      scrollEdges({ scrollLeft: 0, scrollWidth: 658, clientWidth: 658 }),
    ).toEqual({
      left: false,
      right: false,
    });
    expect(scrollEdges()).toEqual({ left: false, right: false });
  });
});

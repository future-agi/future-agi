import { describe, expect, it } from "vitest";
import { unwrapCellValue } from "./datasetCellValue";

describe("unwrapCellValue", () => {
  it("returns blank for a wrapper whose cell_value is null (the empty-cell leak)", () => {
    const wrapper = {
      cell_id: "abc",
      cell_value: null,
      metadata: {},
      status: "empty",
    };
    expect(unwrapCellValue(wrapper)).toBe("");
  });

  it("returns blank for a wrapper whose cell_value is undefined", () => {
    expect(unwrapCellValue({ cell_id: "abc" })).toBe("");
  });

  it("returns the inner value when the wrapper has a cell_value", () => {
    expect(unwrapCellValue({ cell_id: "abc", cell_value: "hello" })).toBe(
      "hello",
    );
  });

  it("preserves falsy-but-present inner values (0, false, empty string)", () => {
    expect(unwrapCellValue({ cell_id: "abc", cell_value: 0 })).toBe(0);
    expect(unwrapCellValue({ cell_id: "abc", cell_value: false })).toBe(false);
    expect(unwrapCellValue({ cell_id: "abc", cell_value: "" })).toBe("");
  });

  it("returns an object cell_value (nested JSON) unchanged", () => {
    const nested = { foo: "bar" };
    expect(unwrapCellValue({ cell_id: "abc", cell_value: nested })).toBe(
      nested,
    );
  });

  it("passes through legacy non-wrapper values as-is", () => {
    expect(unwrapCellValue("raw string")).toBe("raw string");
    expect(unwrapCellValue(42)).toBe(42);
  });

  it("does not treat a plain object without cell_id as a wrapper", () => {
    const plain = { cell_value: "x", foo: 1 };
    expect(unwrapCellValue(plain)).toBe(plain);
  });

  it("returns blank for null/undefined input", () => {
    expect(unwrapCellValue(null)).toBe("");
    expect(unwrapCellValue(undefined)).toBe("");
  });
});

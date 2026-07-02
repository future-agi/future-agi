import { describe, it, expect } from "vitest";

describe("cell value dual-read", () => {
  it("preserves null snake_case instead of falling through to camelCase", () => {
    const valueGetter = (v) => {
      const cell = v?.data?.col1;
      return cell?.cell_value !== undefined ? cell.cell_value : cell?.cellValue;
    };

    expect(valueGetter({ data: { col1: { cell_value: null } } })).toBe(null);
    expect(valueGetter({ data: { col1: { cell_value: "hello" } } })).toBe(
      "hello",
    );
    expect(valueGetter({ data: { col1: { cellValue: "world" } } })).toBe(
      "world",
    );
    expect(valueGetter({ data: { col1: {} } })).toBe(undefined);
  });

  it("preserves null snake_case for column metadata fields", () => {
    const readField = (obj, snake, camel) =>
      obj?.[snake] !== undefined ? obj[snake] : obj?.[camel];

    expect(
      readField({ data_type: null, dataType: "text" }, "data_type", "dataType"),
    ).toBe(null);
    expect(readField({ data_type: "text" }, "data_type", "dataType")).toBe(
      "text",
    );
    expect(readField({ dataType: "text" }, "data_type", "dataType")).toBe(
      "text",
    );
    expect(readField({}, "data_type", "dataType")).toBe(undefined);
  });
});

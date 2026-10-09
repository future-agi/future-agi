import { describe, expect, it } from "vitest";
import { formatMetadataValue, readMetadataValue } from "../formatMetadataValue";

describe("formatMetadataValue (R17-R24, AC7, AC8)", () => {
  it.each([
    [undefined, "-"],
    [null, "-"],
    ["", "-"],
    [false, "false"],
    [true, "true"],
    [0, "0"],
    [1.5, "1.5"],
    [NaN, "NaN"],
    ["acme", "acme"],
    [{ a: 1 }, '{"a":1}'],
    [[1, "two"], '[1,"two"]'],
  ])("formats %j as %j", (value, text) => {
    const result = formatMetadataValue(value);
    expect(result.text).toBe(text);
    expect(result.truncated).toBe(false);
  });

  it("returns no tooltip text for empty values", () => {
    expect(formatMetadataValue(undefined).full).toBeNull();
    expect(formatMetadataValue("").full).toBeNull();
  });

  it("collapses control characters and newlines into single spaces", () => {
    expect(formatMetadataValue("a\nb\r\n\tc\u0000d\u2028e").text).toBe(
      "a b c d e",
    );
  });

  it("previews at 79 characters plus an ellipsis and keeps the full text for the tooltip", () => {
    const value = "x".repeat(81);
    const result = formatMetadataValue(value);
    expect(result.text).toBe(`${"x".repeat(79)}…`);
    expect(result.text).toHaveLength(80);
    expect(result.full).toBe(value);
    expect(result.truncated).toBe(true);
    const exact = formatMetadataValue("y".repeat(80));
    expect(exact.text).toBe("y".repeat(80));
    expect(exact.truncated).toBe(false);
  });

  it("caps the tooltip at 2,000 characters and marks it truncated", () => {
    const value = "z".repeat(2001);
    const result = formatMetadataValue(value);
    expect(result.full).toBe(`${"z".repeat(2000)} [truncated]`);
    expect(result.truncated).toBe(true);
    const big = formatMetadataValue("w".repeat(1_000_000));
    expect(big.text).toHaveLength(80);
    expect(big.full).toHaveLength(2000 + " [truncated]".length);
  });

  it("renders script-like strings literally (AC9)", () => {
    const value = "<img src=x onerror=alert(1)>";
    expect(formatMetadataValue(value).text).toBe(value);
  });

  it("falls back to a marker when JSON serialization throws", () => {
    const evil = {
      toJSON() {
        throw new Error("boom");
      },
    };
    expect(formatMetadataValue(evil).text).toBe("[unserializable]");
  });
});

describe("readMetadataValue (R9, AC7, AC9)", () => {
  it("reads only own properties of a plain object", () => {
    expect(readMetadataValue({ tenant: "acme" }, "tenant")).toBe("acme");
    expect(readMetadataValue({ tenant: false }, "tenant")).toBe(false);
    expect(readMetadataValue({}, "tenant")).toBeUndefined();
    expect(readMetadataValue({ tenant: "x" }, "constructor")).toBeUndefined();
    expect(readMetadataValue({ tenant: "x" }, "toString")).toBeUndefined();
    const polluted = JSON.parse('{"__proto__": {"tenant": "evil"}}');
    expect(readMetadataValue(polluted, "tenant")).toBeUndefined();
  });

  it("treats non-object metadata as absent", () => {
    for (const metadata of [null, undefined, "str", 42, [], true]) {
      expect(readMetadataValue(metadata, "tenant")).toBeUndefined();
    }
  });

  it("does not interpret nested paths", () => {
    expect(readMetadataValue({ a: { b: 1 } }, "a.b")).toBeUndefined();
  });
});

import { describe, expect, it, vi } from "vitest";
import {
  formatMetadataValue,
  MAX_VALUE_UTF16_LENGTH,
  PREVIEW_UTF16_LIMIT,
  readMetadataValue,
} from "../formatMetadataValue";

describe("formatMetadataValue", () => {
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

  it("caps the tooltip at 2,000 characters and marks it truncated up front, where it is always visible", () => {
    const value = "z".repeat(2001);
    const result = formatMetadataValue(value);
    expect(result.full).toBe(`[truncated] ${"z".repeat(2000)}`);
    expect(result.truncated).toBe(true);
    const big = formatMetadataValue("w".repeat(1_000_000));
    expect(big.text).toHaveLength(80);
    expect(big.full).toHaveLength("[truncated] ".length + 2000);
  });

  it("uses logical grapheme limits even when clusters exceed the UTF-16 budget", () => {
    // The raw ceiling bounds pathological clusters while ordinary values keep
    // their grapheme-based limits.
    const fat = `a${"\u0301".repeat(200)}`; // 201 UTF-16 units
    const preview = formatMetadataValue(fat.repeat(100));
    expect(preview.text.length).toBeLessThanOrEqual(PREVIEW_UTF16_LIMIT);
    expect(preview.truncated).toBe(true);

    const mark = `a${"\u0301".repeat(50)}`; // 51 UTF-16 units
    const tooltip = formatMetadataValue(mark.repeat(3000));
    expect(tooltip.full).toHaveLength(
      "[truncated] ".length + MAX_VALUE_UTF16_LENGTH,
    );
    expect(tooltip.full.startsWith("[truncated] ")).toBe(true);
  });

  it("caps a single oversized grapheme at code-point boundaries", () => {
    const value = `a${String.fromCharCode(0x0301).repeat(9000)}`;
    const result = formatMetadataValue(value);
    expect(result.text.length).toBeLessThanOrEqual(PREVIEW_UTF16_LIMIT);
    expect(result.full).toHaveLength(
      "[truncated] ".length + MAX_VALUE_UTF16_LENGTH,
    );
    expect(result.full.startsWith("[truncated] a")).toBe(true);
    expect(result.truncated).toBe(true);
  });

  it.each([
    ["an emoji", "😀"],
    ["a flag", "🇯🇵"],
    ["a ZWJ family", "👨‍👩‍👧"],
  ])(
    "cuts on grapheme clusters so %s at either limit is never split",
    (_label, emoji) => {
      const preview = formatMetadataValue(
        `${"a".repeat(78)}${emoji}${"b".repeat(10)}`,
      );
      expect(preview.text).toBe(`${"a".repeat(78)}${emoji}…`);
      const tooltip = formatMetadataValue(`${"c".repeat(1999)}${emoji}d`);
      expect(tooltip.full).toBe(`[truncated] ${"c".repeat(1999)}${emoji}`);
    },
  );

  it("falls back to code points where Intl.Segmenter is unavailable", async () => {
    vi.stubGlobal(
      "Intl",
      Object.create(Intl, { Segmenter: { value: undefined } }),
    );
    vi.resetModules();
    const { formatMetadataValue: format } = await import(
      "../formatMetadataValue"
    );
    vi.unstubAllGlobals();

    // A surrogate pair stays whole; a flag counts as its two code points.
    expect(format(`${"a".repeat(78)}😀${"b".repeat(10)}`).text).toBe(
      `${"a".repeat(78)}😀…`,
    );
    expect(format(`${"a".repeat(79)}🇯🇵`).text).toBe(`${"a".repeat(79)}…`);
  });

  it("renders script-like strings literally", () => {
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

describe("readMetadataValue", () => {
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

// Hovering a dataset grid cell shows its value formatted for the cell:
// an object, an array or a JSON string reads as indented JSON, not one long
// line; a list of plain numbers (an embedding) stays on one line; plain
// text stays as it is.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { TIP_MAX, tipText } from "../tipText";

describe("grid cell hover text", () => {
  it("indents an object, keeping key order", () => {
    expect(tipText({ b: 1, a: { c: "x", d: [{ e: true }] } })).toBe(
      '{\n  "b": 1,\n  "a": {\n    "c": "x",\n    "d": [\n      {\n        "e": true\n      }\n    ]\n  }\n}',
    );
  });

  it("indents an array of objects", () => {
    expect(tipText([{ role: "user", content: "hi" }])).toBe(
      '[\n  {\n    "role": "user",\n    "content": "hi"\n  }\n]',
    );
  });

  it("indents a JSON string", () => {
    expect(tipText('{"name":"Ada","tags":["x"]}')).toBe(
      '{\n  "name": "Ada",\n  "tags": [\n    "x"\n  ]\n}',
    );
  });

  it("keeps a list of numbers (an embedding) on one line", () => {
    expect(tipText([0.1, -0.2, 3])).toBe("[0.1,-0.2,3]");
    expect(tipText({ vector: [1, 2, 3], n: 3 })).toBe(
      '{\n  "vector": [1,2,3],\n  "n": 3\n}',
    );
    const big = Array.from({ length: 1536 }, (_, i) => i / 1000);
    expect(tipText(big).split("\n")).toHaveLength(1);
  });

  it("matches JSON.stringify for empty values and drops undefined keys", () => {
    expect(tipText({})).toBe("{}");
    expect(tipText([])).toBe("[]");
    expect(tipText({ a: undefined, b: null })).toBe('{\n  "b": null\n}');
  });

  it("keeps plain text and text that only looks like JSON as they are", () => {
    expect(tipText("The refund arrives in 5 days.")).toBe(
      "The refund arrives in 5 days.",
    );
    expect(tipText("{not json")).toBe("{not json");
    expect(tipText("42")).toBe("42");
  });

  it("caps long values", () => {
    const long = { text: "x".repeat(TIP_MAX * 2) };
    const out = tipText(long);
    expect(out.length).toBe(TIP_MAX + 1);
    expect(out.endsWith("…")).toBe(true);
  });

  it("is what the grid cell's hover shows", () => {
    const src = readFileSync(
      resolve(process.cwd(), "src/sections/common/Values/GridValueSummary.jsx"),
      "utf8",
    );
    const tip = src.slice(src.indexOf("<CustomTooltip"));
    expect(tip).toMatch(/\{tipText\(\s*sniffed\.kind === "chat"/);
  });
});

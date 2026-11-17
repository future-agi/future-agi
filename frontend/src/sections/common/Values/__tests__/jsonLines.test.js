import { describe, expect, it } from "vitest";
import {
  lineCountText,
  parseJsonText,
  prettyJson,
  toCodeLines,
  tokenizeJsonLine,
  wordsOf,
} from "../jsonLines";

describe("codeLines", () => {
  it("pretty-prints JSON text, plain text and missing values", () => {
    expect(prettyJson('[{"name":"lookup_order","args":{"id":"A-4411"}}]')).toBe(
      '[\n  {\n    "name": "lookup_order",\n    "args": {\n      "id": "A-4411"\n    }\n  }\n]',
    );
    expect(prettyJson("I was charged twice")).toBe('"I was charged twice"');
    expect(prettyJson(null)).toBe("null");
    expect(prettyJson(undefined)).toBe("null");
    expect(prettyJson(0.8)).toBe("0.8");
    expect(prettyJson("{not json")).toBe('"{not json"');
  });

  it("parses only object / array JSON text", () => {
    expect(parseJsonText('{"a":1}')).toEqual({ a: 1 });
    expect(parseJsonText("42")).toBeUndefined();
    expect(parseJsonText(42)).toBeUndefined();
  });

  it("tokenises a line losslessly", () => {
    const line = '    "id": "A-4411", "n": 2, "ok": true, "x": null';
    const toks = tokenizeJsonLine(line);
    expect(toks.map((t) => t.text).join("")).toBe(line);
    expect(toks.filter((t) => t.t === "key").map((t) => t.text)).toEqual([
      '"id"',
      '"n"',
      '"ok"',
      '"x"',
    ]);
    expect(toks.find((t) => t.t === "string").text).toBe('"A-4411"');
    expect(toks.find((t) => t.t === "number").text).toBe("2");
    expect(toks.find((t) => t.t === "boolean").text).toBe("true");
    expect(toks.find((t) => t.t === "null").text).toBe("null");
  });

  it("numbers lines from 1", () => {
    const lines = toCodeLines('{\n  "a": 1\n}');
    expect(lines.map((l) => l.n)).toEqual([1, 2, 3]);
    expect(toCodeLines("a\nb", { json: false })[1].tokens).toEqual([
      { t: "punct", text: "b" },
    ]);
  });

  it("splits words only at spaces", () => {
    expect(wordsOf('"refund A-4411 now"')).toEqual([
      '"refund',
      " ",
      "A-4411",
      " ",
      'now"',
    ]);
    expect(lineCountText(1)).toBe("1 line");
    expect(lineCountText(12)).toBe("12 lines");
  });
});

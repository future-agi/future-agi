import { describe, expect, it } from "vitest";
import { editStats, jsonSyntaxError } from "../editValue";
import { charDelta } from "src/sections/common/ValueViewer/diffSegments";

describe("jsonSyntaxError (RV#13)", () => {
  it("is null for valid JSON", () => {
    expect(jsonSyntaxError('{"x":[1,{"y":true}],"z":-1.5e3}')).toBeNull();
    expect(jsonSyntaxError(" [ ] ")).toBeNull();
  });
  it("points at the first error with line and column", () => {
    expect(jsonSyntaxError('{"a": 1,\n "b": }')).toEqual({
      message: "Expected a value, found '}'",
      line: 2,
      col: 7,
    });
    expect(jsonSyntaxError("[1,2")).toMatchObject({ line: 1, col: 5 });
    expect(jsonSyntaxError('{"a" 1}').message).toBe(
      "Expected ':' after the key, found '1'",
    );
    expect(jsonSyntaxError('"abc').message).toBe("Unterminated string");
    expect(jsonSyntaxError('{"a":1} x').message).toBe(
      "Unexpected 'x' after the value",
    );
  });
});

describe("editStats (RV#13)", () => {
  it("counts characters and estimates tokens", () => {
    expect(editStats("hello world, how are you", "text")).toEqual({
      chars: 24,
      tokens: 6,
      json: null,
    });
    expect(editStats("", "text")).toEqual({ chars: 0, tokens: 0, json: null });
  });
  it("checks JSON fields only", () => {
    expect(editStats('{"a":1}', "json").json).toEqual({ ok: true });
    expect(editStats("{", "json").json.ok).toBe(false);
    expect(editStats("   ", "json").json).toEqual({ ok: true });
  });
});

describe("charDelta (RV#12)", () => {
  it("counts added and removed characters around a common prefix/suffix", () => {
    expect(charDelta("Thanks", "Thanks!!!")).toEqual({ added: 3, removed: 0 });
    expect(charDelta("abc", "abd")).toEqual({ added: 1, removed: 1 });
    expect(charDelta(null, "ab")).toEqual({ added: 2, removed: 0 });
    expect(charDelta("same", "same")).toEqual({ added: 0, removed: 0 });
  });
});

import { describe, expect, it } from "vitest";
import { schemaProblems, validateRow, validateValue } from "../schemaValidate";

describe("declared schema preview", () => {
  it("compares JSON enum values structurally rather than by reference", () => {
    const schema = { enum: [{ a: 1, b: [true, false] }] };
    expect(validateValue({ b: [true, false], a: 1 }, schema)).toEqual([]);
    expect(validateValue({ b: [1, false], a: 1 }, schema)).not.toEqual([]);
  });
  it("does not silently skip missing required columns or null typed values", () => {
    const declared = {
      type: "object",
      required: ["missing"],
      properties: { text: { type: "string" } },
    };
    const result = validateRow({ text: null }, declared, {
      columns: [{ name: "text" }],
    });
    expect(result.ok).toBe(false);
    expect(result.issues.map((issue) => issue.path)).toEqual([
      "missing",
      "text",
    ]);
  });

  it("checks additional properties across the actual row", () => {
    const result = validateRow(
      { expected: "ok", extra: "x" },
      {
        type: "object",
        properties: { expected: { type: "string" } },
        additionalProperties: false,
      },
      { columns: [{ name: "expected" }, { name: "extra" }] },
    );
    expect(result.issues).toEqual([
      { path: "extra", message: "extra is not allowed" },
    ]);
  });

  it("interprets typed stored numbers and booleans without coercing text columns", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "number" },
        yes: { type: "boolean" },
        no: { type: "boolean" },
      },
    };
    const values = { n: "1e3", yes: "true", no: "false" };
    expect(
      validateRow(values, schema, {
        columns: [
          { name: "n", number: true },
          { name: "yes", boolean: true },
          { name: "no", boolean: true },
        ],
      }).ok,
    ).toBe(true);
    expect(
      validateRow(values, schema, {
        columns: [{ name: "n" }, { name: "yes" }, { name: "no" }],
      }).ok,
    ).toBe(false);
  });

  it("reports unsupported local-only schema keywords before saving", () => {
    expect(
      schemaProblems({
        type: "object",
        properties: { question: { $ref: "https://example.invalid/schema" } },
      }),
    ).toEqual(['question: unsupported keyword "$ref"']);
  });
});

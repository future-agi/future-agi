import { describe, expect, it } from "vitest";
import { detectValueKind } from "src/sections/common/ValueViewer/valueKind";
import { importBlockReason, parseImport } from "../importFile";

const refusal =
  "CSV rows have inconsistent column counts. Correct the file and upload it again; no rows will be imported.";
const blockReason = (parsed) =>
  importBlockReason({
    name: "Imported records",
    hasFile: true,
    parsed,
    keptColumns: parsed.columns.length,
  });

describe("whole-file CSV admission", () => {
  it.each([",", ";", "\t", "|"])(
    "refuses malformed records with separator %s while preserving diagnostics",
    (delimiter) => {
      const parsed = parseImport(
        `id${delimiter}name\n1${delimiter}a\nlonely\n2${delimiter}b\n`,
        "csv",
        { rejectMalformedCsv: true },
      );

      expect(parsed.problem).toBe(refusal);
      expect(blockReason(parsed)).toBe(refusal);
      expect(parsed.rows).toEqual([
        ["1", "a"],
        ["2", "b"],
      ]);
      expect(parsed.rejected).toEqual([
        {
          line: 3,
          reason: "Expected 2 values, found 1",
          text: "lonely",
        },
      ]);
    },
  );

  it("keeps corrected CSV importable", () => {
    const parsed = parseImport("id,name\n1,a\n2,b\n", "csv", {
      rejectMalformedCsv: true,
    });

    expect(parsed.problem).toBeNull();
    expect(parsed.rejected).toEqual([]);
    expect(blockReason(parsed)).toBeNull();
  });

  it.each(["“", "”", "„", "‟"])(
    "tries normalized %s wrappers before refusing an inconsistent raw parse",
    (quote) => {
      const parsed = parseImport(
        `id,name\n1,plain\n2,${quote}b,c${quote}\n`,
        "csv",
        { rejectMalformedCsv: true },
      );

      expect(parsed.columns).toEqual(["id", "name"]);
      expect(parsed.rows).toEqual([
        ["1", "plain"],
        ["2", "b,c"],
      ]);
      expect(parsed.rejected).toEqual([]);
      expect(parsed.errors).toEqual([]);
      expect(parsed.total).toBe(2);
      expect(parsed.problem).toBeNull();
      expect(blockReason(parsed)).toBeNull();
    },
  );

  it.each([",", ";", "\t", "|"])(
    "chooses the normalized consistent candidate for separator %s",
    (delimiter) => {
      const parsed = parseImport(
        `id${delimiter}name\n1${delimiter}plain\n2${delimiter}“b${delimiter}c”\n`,
        "csv",
        { rejectMalformedCsv: true },
      );

      expect(parsed.columns).toEqual(["id", "name"]);
      expect(parsed.rows).toEqual([
        ["1", "plain"],
        ["2", `b${delimiter}c`],
      ]);
      expect(parsed.rejected).toEqual([]);
      expect(blockReason(parsed)).toBeNull();
    },
  );

  it.each([
    ["id,name\n1,plain\n2,“literal”\n", "“literal”"],
    ['id,name\n1,plain\n2,"Say “hi”"\n', "Say “hi”"],
    ['id,name\n1,plain\n2,"Say “hi, there”"\n', "Say “hi, there”"],
  ])("keeps raw content when normalization is not wider: %s", (text, value) => {
    const parsed = parseImport(text, "csv", { rejectMalformedCsv: true });

    expect(parsed.columns).toEqual(["id", "name"]);
    expect(parsed.rows).toEqual([
      ["1", "plain"],
      ["2", value],
    ]);
    expect(parsed.rejected).toEqual([]);
    expect(blockReason(parsed)).toBeNull();
  });

  it("refuses ragged rows after trying both quote candidates", () => {
    const parsed = parseImport("id,name\n1,plain\n2,“b,c”\nlonely\n", "csv", {
      rejectMalformedCsv: true,
    });

    expect(parsed.problem).toBe(refusal);
    expect(blockReason(parsed)).toBe(refusal);
    expect(parsed.rejected).toEqual([
      { line: 3, reason: "Expected 2 values, found 3", text: "2,“b,c”" },
      { line: 4, reason: "Expected 2 values, found 1", text: "lonely" },
    ]);
  });

  it("preserves supported JSONL skipping", () => {
    const parsed = parseImport('{"input":"hello"}\nnot a record\n', "jsonl", {
      rejectMalformedCsv: true,
    });

    expect(parsed.rows).toEqual([["hello"]]);
    expect(parsed.problem).toBeNull();
    expect(blockReason(parsed)).toBeNull();
    expect(parsed.rejected).toEqual([
      { line: 2, reason: "Not a JSON object", text: "not a record" },
    ]);
  });

  it("leaves filtered-record consumers unchanged unless opted in", () => {
    const parsed = parseImport("id,name\n1,a\nlonely\n2,b\n", "csv");

    expect(parsed.problem).toBeNull();
    expect(blockReason(parsed)).toBeNull();
    expect(parsed.total).toBe(2);
  });

  it("recognizes the structured chat text stored by the Hub worker", () => {
    const stored = '[{"role": "user", "content": "Say \\"hi\\"", "ok": false}]';
    expect(detectValueKind(stored, "array")).toBe("chat");
  });
});

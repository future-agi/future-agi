import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import {
  docSummary,
  expectedFieldsOf,
  fieldRowsOf,
  jsonObjectOf,
  laneScores,
} from "../docModel";

const contract = (warranty) =>
  JSON.stringify({
    customer: "Tomás García",
    order_id: "A-7723",
    total: 63,
    warranty_months: warranty,
  });
const evalFields = (got) => ({
  scores: {
    "eval:field_match": {
      eval_details: {
        fields: {
          customer: { expected: "Tomás García", got: got.customer },
          order_id: { expected: "A-7723" },
          total: { expected: 63 },
          warranty_months: { expected: 12 },
        },
      },
    },
  },
});
const lanes = (bWarranty = 24) => [
  {
    runId: "a",
    label: "#1A gpt-4.1",
    isBaseline: true,
    value: contract(12),
    result: evalFields({}),
  },
  {
    runId: "b",
    label: "#1B gemini-2.5-flash",
    value: contract(bWarranty),
    result: evalFields({}),
  },
];

describe("docModel", () => {
  it("parses JSON objects only", () => {
    expect(jsonObjectOf('{"a":1}')).toEqual({ a: 1 });
    expect(jsonObjectOf("[1]")).toBeNull();
    expect(jsonObjectOf("plain")).toBeNull();
    expect(jsonObjectOf({ a: 1 })).toEqual({ a: 1 });
  });

  it("reads expected fields from the lane, the lens, or the eval details", () => {
    expect(expectedFieldsOf({ expected: { a: 1 } })).toEqual({
      fields: { a: 1 },
      source: "row",
    });
    expect(expectedFieldsOf({}, '{"a":2}').fields).toEqual({ a: 2 });
    expect(expectedFieldsOf(lanes()[0]).source).toBe("eval");
    expect(expectedFieldsOf({ result: { scores: {} } })).toBeNull();
  });

  it("marks each lane ✓ / ✗ per field with a number diff", () => {
    const t = fieldRowsOf({ lanes: lanes() });
    expect(t.against).toBe("expected");
    const w = t.rows.find((r) => r.key === "warranty_months");
    expect(w.cells.map((c) => c.match)).toEqual([true, false]);
    expect(w.cells[1].numDelta).toBe(12);
    expect(laneScores(t).map((s) => `${s.right}/${s.total}`)).toEqual([
      "4/4",
      "3/4",
    ]);
  });

  it("summary names the misses (MM #1 row 23)", () => {
    expect(docSummary(fieldRowsOf({ lanes: lanes() }), lanes())).toBe(
      "gemini-2.5-flash got 3 of 4 fields · warranty_months 24 (expected 12)",
    );
  });

  it("follows the data: a different miss changes the sentence (§C10)", () => {
    const l = lanes(36);
    expect(docSummary(fieldRowsOf({ lanes: l }), l)).toBe(
      "gemini-2.5-flash got 3 of 4 fields · warranty_months 36 (expected 12)",
    );
    const ok = lanes(12);
    expect(docSummary(fieldRowsOf({ lanes: ok }), ok)).toBe(
      "Every run got all 4 fields",
    );
  });

  it("without expected fields it compares against the baseline, no verdict", () => {
    const l = lanes().map((x) => ({ ...x, result: undefined }));
    const t = fieldRowsOf({ lanes: l });
    expect(t.against).toBe("baseline");
    const w = t.rows.find((r) => r.key === "warranty_months");
    expect(w.cells.map((c) => c.match)).toEqual([null, false]);
    expect(docSummary(t, l)).toBeNull();
  });

  it("counts a missing field as a miss", () => {
    const l = lanes();
    l[1].value = JSON.stringify({
      customer: "Tomás García",
      order_id: "A-7723",
      total: 63,
    });
    const t = fieldRowsOf({ lanes: l });
    expect(docSummary(t, l)).toBe(
      "gemini-2.5-flash got 3 of 4 fields · warranty_months missing (expected 12)",
    );
  });

  it("puts no digit literal in its sentence templates", () => {
    const src = fs.readFileSync(
      path.resolve(process.cwd(), "src/sections/common/Lenses/docModel.js"),
      "utf8",
    );
    const code = src.replace(/^\s*(\/\/|\/?\*).*$/gm, "");
    (code.match(/`[^`]*`/g) ?? []).forEach((t) =>
      expect(t.replace(/\$\{[^}]*\}/g, "")).not.toMatch(/[2-9]/),
    );
  });
});

describe("docModel expected guards", () => {
  it("ignores a free-text expected that names no output field", () => {
    const l = lanes().map((x) => ({ ...x, result: undefined }));
    const rowExpected = {
      value: { expected: '{"answer":"Yes","citations":[]}' },
      primary: "expected",
    };
    expect(fieldRowsOf({ lanes: l, expected: rowExpected }).against).toBe(
      "baseline",
    );
    const fieldsExpected = {
      value: { expected: { warranty_months: 12 } },
      primary: "expected",
    };
    expect(fieldRowsOf({ lanes: l, expected: fieldsExpected }).against).toBe(
      "expected",
    );
  });
});

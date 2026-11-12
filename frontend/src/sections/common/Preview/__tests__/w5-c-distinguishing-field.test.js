import { describe, expect, it } from "vitest";
import {
  PREVIEW_SAMPLE_ROWS,
  chooseField,
  inputFields,
  inputsPreviewChoice,
  isProse,
  jsonCellValue,
  normaliseValue,
  previewFieldText,
  previewLabel,
} from "../distinguishingField";

const F = [
  { key: "system", label: "system" },
  { key: "customer_message", label: "customer_message" },
  { key: "intent", label: "intent" },
];

describe("w5-c chooseField", () => {
  it("picks the field with the most distinct values and names the constant one", () => {
    const rows = [
      {
        system: "You are helpful.",
        customer_message: "Where is my order?",
        intent: "order",
      },
      {
        system: "You are helpful. ",
        customer_message: "Refund please",
        intent: "refund",
      },
      {
        system: "you are  helpful.",
        customer_message: "Cancel it",
        intent: "order",
      },
    ];
    const c = chooseField(rows, F);
    expect(c.field).toBe("customer_message");
    expect(c.distinct).toEqual({ system: 1, customer_message: 3, intent: 2 });
    expect(c.same).toEqual([
      { key: "system", label: "system", value: "You are helpful." },
    ]);
    expect(c.sampled).toBe(3);
  });

  it("ties go to the earlier column", () => {
    const rows = [
      { system: "a", customer_message: "x", intent: "p" },
      { system: "b", customer_message: "y", intent: "q" },
    ];
    expect(chooseField(rows, F).field).toBe("system");
    expect(chooseField(rows, [F[2], F[1]]).field).toBe("intent");
  });

  it("empty values count as one value and an all-empty field is never chosen or called the same", () => {
    const rows = [
      { system: "", customer_message: null, intent: [] },
      { system: null, customer_message: "", intent: {} },
    ];
    const c = chooseField(rows, F);
    expect(c.field).toBeNull();
    expect(c.same).toEqual([]);
    expect(normaliseValue({})).toBe("");
    expect(normaliseValue([])).toBe("");
    expect(normaliseValue(undefined)).toBe("");
  });

  it("a JSON column's text cells read as values: all `[]` is empty, never the same (M7)", () => {
    const rows = [
      { conversation: { cell_value: "[]" }, m: "a" },
      { conversation: "[]", m: "b" },
      { conversation: " {} ", m: "c" },
    ];
    const fields = [
      { key: "conversation", label: "conversation" },
      { key: "m", label: "m" },
    ];
    const read = (row, key) =>
      key === "conversation" ? jsonCellValue(row[key]) : row[key];
    expect(jsonCellValue('[{"role":"user"}]')).toEqual([{ role: "user" }]);
    expect(jsonCellValue("not [json")).toBe("not [json");
    expect(jsonCellValue("plain")).toBe("plain");
    const r = chooseField(rows, fields, { read });
    expect(r.same).toEqual([]);
    expect(r.field).toBe("m");
  });

  it("a single row compares with nothing: no field is the same; the first filled field is chosen", () => {
    const c = chooseField(
      [{ system: "", customer_message: "hi", intent: "x" }],
      F,
    );
    expect(c.same).toEqual([]);
    expect(c.field).toBe("customer_message");
  });

  it("no rows and no fields", () => {
    expect(chooseField([], F)).toEqual({
      field: null,
      distinct: { system: 0, customer_message: 0, intent: 0 },
      same: [],
      sampled: 0,
    });
    expect(chooseField(null, null)).toEqual({
      field: null,
      distinct: {},
      same: [],
      sampled: 0,
    });
  });

  it("reads table cells ({cell_value}) and objects by key-sorted JSON", () => {
    const rows = [
      { a: { cell_value: { x: 1, y: 2 } }, b: { cell_value: "same" } },
      { a: { cell_value: { y: 2, x: 1 } }, b: { cell_value: "same" } },
      { a: { cell_value: { y: 3, x: 1 } }, b: { cell_value: "same" } },
    ];
    const c = chooseField(rows, [{ key: "b" }, { key: "a" }]);
    expect(c.distinct).toEqual({ b: 1, a: 2 });
    expect(c.field).toBe("a");
    expect(c.same.map((s) => s.key)).toEqual(["b"]);
  });

  it("reads at most PREVIEW_SAMPLE_ROWS rows", () => {
    const rows = Array.from({ length: PREVIEW_SAMPLE_ROWS + 50 }, (_, i) => ({
      id: String(i),
    }));
    const c = chooseField(rows, [{ key: "id" }]);
    expect(c.sampled).toBe(PREVIEW_SAMPLE_ROWS);
    expect(c.distinct.id).toBe(PREVIEW_SAMPLE_ROWS);
  });

  it("uses a custom accessor", () => {
    const rows = [{ v: { m: "1" } }, { v: { m: "2" } }];
    const c = chooseField(rows, [{ key: "m" }], { read: (r, k) => r.v[k] });
    expect(c.field).toBe("m");
  });

  it("previewLabel names the field and counts the others", () => {
    expect(previewLabel({ field: "customer_message" }, F)).toBe(
      "customer_message · +2 fields",
    );
    expect(previewLabel({ field: "system" }, [F[0], F[1]])).toBe(
      "system · +1 field",
    );
    expect(previewLabel({ field: "system" }, [F[0]])).toBe("system");
    expect(previewLabel({ field: null }, F)).toBe("");
  });
});

describe("w5-c results input preview (C-H6)", () => {
  const rows = [
    { inputs: { system: "Be brief.", customer_message: "Refund A-1", n: 1 } },
    {
      inputs: { system: "Be brief.", customer_message: "Where is A-2?", n: 1 },
    },
    {
      inputs: { system: " be  BRIEF. ", customer_message: "", extra: { a: 1 } },
    },
  ];
  it("compares the view's input fields in first-seen order", () => {
    expect(inputFields(rows).map((f) => f.key)).toEqual([
      "system",
      "customer_message",
      "n",
      "extra",
    ]);
    expect(inputFields([])).toEqual([]);
    expect(inputFields([{ inputs: null }, {}])).toEqual([]);
  });
  it("previews the field that differs most, never the constant system text", () => {
    const choice = inputsPreviewChoice(rows);
    expect(choice.field).toBe("customer_message");
    // `n` is missing on one row: an empty value is a value, so it differs.
    expect(choice.same.map((s) => s.key)).toEqual(["system"]);
    expect(previewLabel(choice, choice.fields)).toBe(
      "customer_message · +3 fields",
    );
  });
  it("gives a row's text for the field, or null to keep the usual text", () => {
    expect(previewFieldText(rows[0].inputs, "customer_message")).toBe(
      "Refund A-1",
    );
    expect(previewFieldText(rows[2].inputs, "customer_message")).toBeNull();
    expect(previewFieldText(rows[2].inputs, "extra")).toBe('{"a":1}');
    expect(previewFieldText(rows[0].inputs, null)).toBeNull();
    expect(previewFieldText(null, "system")).toBeNull();
  });
});

describe("w5-c results preview prefers prose", () => {
  it("tells prose from links, data URIs and serialised JSON", () => {
    expect(isProse("Where is my order?")).toBe(true);
    expect(isProse("[draft] a reply")).toBe(true);
    expect(isProse("https://cdn.example.org/p/1.png")).toBe(false);
    expect(isProse("data:image/png;base64,AAAA")).toBe(false);
    expect(isProse('[{"chunk_id":"c1"}]')).toBe(false);
    expect(isProse({ a: 1 })).toBe(false);
    expect(isProse("  ")).toBe(false);
  });
  it("keeps a differing question over chunks and image links with more values", () => {
    const rows = Array.from({ length: 6 }, (_, i) => ({
      inputs: {
        question: i < 3 ? "How long is the warranty?" : "Can I cancel?",
        context: [{ chunk_id: `c${i}`, text: `chunk ${i}` }],
        page_image: `https://cdn.example.org/p/${i}.png`,
      },
    }));
    expect(inputsPreviewChoice(rows).field).toBe("question");
  });
  it("skips a constant prose field for the prose field that differs", () => {
    const rows = [
      { inputs: { system: "Be brief.", customer_message: "A", meta: "{}" } },
      { inputs: { system: "Be brief.", customer_message: "B", meta: "{}" } },
    ];
    const c = inputsPreviewChoice(rows);
    expect(c.field).toBe("customer_message");
    expect(c.same.map((x) => x.key)).toEqual(["system", "meta"]);
  });
  it("falls back to every field when no prose field differs", () => {
    const rows = [
      { inputs: { system: "Be brief.", payload: { id: 1 } } },
      { inputs: { system: "Be brief.", payload: { id: 2 } } },
    ];
    expect(inputsPreviewChoice(rows).field).toBe("payload");
  });
});

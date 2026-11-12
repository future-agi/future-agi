import { describe, expect, it } from "vitest";
import { parsePasted } from "../pasteRows";
import {
  CREATE,
  SKIP,
  addLabel,
  blockedReason,
  defaultMapping,
  mappedRows,
  mappingSummary,
} from "../pasteMapping";

// IT-R3 G5 P4: paste any sheet.
const COLS = [
  { id: "c-msg", name: "customer_message", role: "input" },
  { id: "c-exp", name: "expected_reply", role: "expected" },
];

const META_HEADERS = [
  "category",
  "tag",
  "tags",
  "intent",
  "topic",
  "label",
  "source",
  "tier",
  "notes",
  "note",
  "priority",
  "language",
  "locale",
  "channel",
];

describe("pasteMapping", () => {
  it.each(META_HEADERS)(
    "creates a column for %s when metadata is unsupported",
    (header) => {
      const mapping = defaultMapping([header], COLS, { allowMetadata: false });
      expect(mapping).toEqual({ [header]: CREATE });
      expect(
        mappedRows([["value"]], [header], mapping, { [header]: "new-column" }),
      ).toEqual([{ cells: { "new-column": "value" }, metadata: {} }]);
    },
  );

  it("reuses matching columns and creates unmatched headers without metadata", () => {
    const headers = ["Category", "category", "Question", "notes"];
    const columns = [...COLS, { id: "c-category", name: "category" }];
    const mapping = defaultMapping(headers, columns, { allowMetadata: false });
    expect(mapping).toEqual({
      Category: "c-category",
      category: CREATE,
      Question: "c-msg",
      notes: CREATE,
    });
    expect(mappingSummary(headers, mapping, columns)).toEqual({
      fills: ["category", "customer_message"],
      metadata: [],
      create: ["category", "notes"],
      skip: [],
    });
  });

  it("keeps metadata defaults when supported", () => {
    expect(defaultMapping(META_HEADERS, COLS, { allowMetadata: true })).toEqual(
      Object.fromEntries(META_HEADERS.map((header) => [header, `metadata.${header}`])),
    );
  });

  it("guesses question / answer / category by meaning", () => {
    const m = defaultMapping(["question", "answer", "category"], COLS);
    expect(m).toEqual({
      question: "c-msg",
      answer: "c-exp",
      category: "metadata.category",
    });
  });

  it("creates every column when nothing matches", () => {
    const headers = ["sku", "colour", "warehouse"];
    const m = defaultMapping(headers, COLS);
    expect(Object.values(m)).toEqual([CREATE, CREATE, CREATE]);
    const s = mappingSummary(headers, m, COLS);
    expect(addLabel(2, s)).toBe("Add 2 rows · 3 new columns");
  });

  it("builds cells and metadata per row", () => {
    const p = parsePasted(
      "question\tanswer\tcategory\nWhere is A-4410?\tTomorrow.\tshipping",
    );
    expect(p.columns).toEqual(["question", "answer", "category"]);
    const m = defaultMapping(p.columns, COLS);
    expect(mappedRows(p.rows, p.columns, m)).toEqual([
      {
        cells: { "c-msg": "Where is A-4410?", "c-exp": "Tomorrow." },
        metadata: { category: "shipping" },
      },
    ]);
  });

  it("never blocks without saying why", () => {
    const s = mappingSummary(["a"], { a: SKIP }, COLS);
    expect(blockedReason({ text: "a\n1", n: 1, summary: s })).toMatch(/Skip/);
    expect(blockedReason({ text: "", n: 0, summary: s })).toBe(
      "Paste rows first",
    );
  });
});

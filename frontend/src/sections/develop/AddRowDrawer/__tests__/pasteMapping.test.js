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

describe("pasteMapping", () => {
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

// options-truth L3 G7 W7: the footer counts rows without an expected value
// (saved and new), never "each has an expected result" when some do not.
import { describe, expect, it } from "vitest";
import { dataFooterText, missingExpected } from "../dataHelp";

const columns = (empty) => [
  { id: "c1", name: "customer_message", role: "input" },
  {
    id: "c3",
    name: "expected_reply",
    role: "expected",
    stats: { empty_count: empty },
  },
];

describe("footer: rows without an expected value", () => {
  it("counts saved and pending rows by the expected column", () => {
    expect(missingExpected(columns(4))).toEqual({
      column: "expected_reply",
      missing: 4,
      pending: 0,
    });
    const pending = [{ cells: { c1: "hi" } }, { cells: { c1: "x", c3: "ok" } }];
    expect(missingExpected(columns(4), pending)).toEqual({
      column: "expected_reply",
      missing: 5,
      pending: 1,
    });
  });
  it("the line names the gap instead of claiming every row has one", () => {
    const full = dataFooterText({
      kind: "rows",
      total: 248,
      columns: columns(0),
    });
    expect(full).toBe("248 rows · each has an input and an expected result");
    const gap = dataFooterText({
      kind: "rows",
      total: 248,
      columns: columns(4),
      added: 2,
      pendingRows: [{ cells: {} }, { cells: { c3: "yes" } }],
    });
    expect(gap).not.toMatch(/expected result/);
    expect(gap).toBe(
      "248 rows + 2 new, unsaved · each has an input · 5 rows (1 new) have no expected_reply",
    );
  });
});

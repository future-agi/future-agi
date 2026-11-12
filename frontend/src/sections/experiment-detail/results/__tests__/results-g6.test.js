// L7c g6: results grid pure words — copy-link toast, Worse tooltip scope,
// reviewed count per pair, cross-mapped columns.
import { describe, expect, it } from "vitest";
import { shareLinkSummary } from "../shareLink";
import { anyWorseTip } from "../ResultsToolbar";
import { pairOf, reviewedText } from "../reviewMarks";
import { crossMappedColumns, crossMapText } from "../addToDatasetModel";

describe("copy link toast (grid:copy-link)", () => {
  it("never says `row Row 141` and names the search", () => {
    const t = shareLinkSummary({
      runCount: 2,
      baselineLabel: "#2A",
      tab: "data",
      filters: 1,
      rowLabel: "Row 141",
      search: "refund",
    });
    expect(t).toBe(
      '2 runs · baseline #2A · Data · 1 filter · search "refund" · Row 141 open',
    );
    expect(t).not.toMatch(/row Row/);
    expect(shareLinkSummary({ runCount: 1, rowLabel: "T12" })).toMatch(
      /row T12 open$/,
    );
  });
});

describe("Worse tooltip (grid:show)", () => {
  it("names each number's scope with several candidates", () => {
    expect(
      anyWorseTip({
        metricName: "Correctness",
        worse: 4,
        total: 14,
        oneCandidate: false,
        leadLabel: "#2B",
      }),
    ).toBe("14 rows worse on any eval for any run · 4 on Correctness for #2B");
    expect(
      anyWorseTip({
        metricName: "Correctness",
        worse: 3,
        total: 9,
        oneCandidate: true,
      }),
    ).toBe("9 rows worse on any eval · 3 on Correctness");
  });
});

describe("N reviewed (status:population)", () => {
  it("counts only votes on the pair in view", () => {
    const marks = {
      r1: { choice: "A", pair: pairOf("3A", "4A") },
      r2: { choice: "B", pair: pairOf("2A", "2B") },
    };
    expect(reviewedText(marks, ["r1", "r2"], pairOf("4A", "3A"))).toBe(
      "1/2 reviewed",
    );
    expect(reviewedText(marks, ["r2"], pairOf("3A", "4A"))).toBeNull();
    expect(reviewedText(marks, ["r1", "r2"])).toBe("2/2 reviewed");
  });
});

describe("cross-mapped columns (bulk:tag-rows)", () => {
  it("flags a column that lands under another name or role", () => {
    const src = [
      { id: "s1", name: "expected_reply", role: "expected" },
      { id: "s2", name: "question" },
    ];
    const tgt = [
      { id: "t1", name: "question", role: "input" },
      { id: "t2", name: "expected_reply", role: "expected" },
    ];
    const pairs = crossMappedColumns(src, tgt, { s1: "t1", s2: "t1" });
    expect(pairs).toEqual([
      { from: "expected_reply", to: "question", role: "input" },
    ]);
    expect(crossMapText(pairs)).toMatch(
      /^1 column goes into a column with another name or role: expected_reply → question \(input\)/,
    );
    expect(crossMappedColumns(src, tgt, { s1: "t2" })).toEqual([]);
  });
});

describe("inputs used (cell:inputs-used)", () => {
  it("lists what the eval read; the output line never claims empty", async () => {
    const { inputsUsedLines } = await import("../drawer/inputsUsedModel");
    const lines = inputsUsedLines({
      output: { column: "run output", value: null },
      expected: { column: "expected_reply", value: "Refunded" },
      input: { column: "customer_message", value: "Charged twice" },
    });
    expect(lines).toEqual([
      "Read expected ← expected_reply: 'Refunded'",
      "Read input ← customer_message: 'Charged twice'",
      "Read output ← run output",
    ]);
  });
});

// W5 C3: when `Select all N matching rows` shows, and the Ask Falcon
// context (rows by reference, the chip text).
import { describe, expect, it } from "vitest";
import { allSelectedText, selectAllOffer } from "../selectAllMatching";
import {
  rowsContextLabel,
  rowsFalconContext,
  rowsPill,
  rowsQuestion,
} from "../askFalconAboutRows";

describe("w5-c select all matching", () => {
  it("offers only when every loaded row is selected and the view has more", () => {
    expect(
      selectAllOffer({ selectedCount: 1000, loadedCount: 1000, total: 4800 }),
    ).toEqual({
      total: 4800,
      text: "Select all 4,800 matching rows",
    });
    expect(
      selectAllOffer({ selectedCount: 999, loadedCount: 1000, total: 4800 }),
    ).toBeNull();
    expect(
      selectAllOffer({ selectedCount: 238, loadedCount: 238, total: 238 }),
    ).toBeNull();
    expect(
      selectAllOffer({ selectedCount: 4800, loadedCount: 1000, total: 4800 }),
    ).toBeNull();
    expect(
      selectAllOffer({ selectedCount: 0, loadedCount: 0, total: 10 }),
    ).toBeNull();
    expect(selectAllOffer({ selectedCount: 5, loadedCount: 5 })).toBeNull();
  });

  it("says when the whole view is selected", () => {
    expect(allSelectedText(1)).toBe("All 1 matching row selected");
    expect(allSelectedText(1200)).toBe("All 1,200 matching rows selected");
  });
});

describe("w5-c ask Falcon about rows", () => {
  const runs = [
    { run_id: "a", label: "#2A" },
    { run_id: "b", label: "#2B" },
  ];
  it("names the rows and runs on the chip", () => {
    expect(rowsContextLabel(6, runs, "a")).toBe("6 rows from #2B vs #2A");
    expect(rowsContextLabel(1, [runs[0]], "a")).toBe("1 row from #2A");
    expect(rowsContextLabel(3, runs, null)).toBe("3 rows from #2A, #2B");
    expect(rowsContextLabel(2, [], null)).toBe("2 rows");
  });

  it("carries ids only, deduplicated, as the results rows pill", () => {
    expect(rowsPill(["x", "y", "x"], "L")).toEqual({
      type: "rows",
      ids: ["x", "y"],
      label: "L",
    });
    const ctx = rowsFalconContext({
      datasetId: "d",
      experimentId: "e",
      runs: [
        { run_id: "a", label: "#2A", output: "long text" },
        { run_id: "b", label: "#2B" },
      ],
      baselineId: "a",
      rowIds: ["r1", "r2", "r1"],
    });
    expect(ctx.runs).toEqual([
      { run_id: "a", label: "#2A" },
      { run_id: "b", label: "#2B" },
    ]);
    expect(ctx.filters).toEqual([
      { type: "rows", ids: ["r1", "r2"], label: "2 rows from #2B vs #2A" },
    ]);
    expect(JSON.stringify(ctx)).not.toContain("long text");
    expect(rowsQuestion(1)).toBe("What happened on this row?");
    expect(rowsQuestion(6)).toBe("What do these 6 rows have in common?");
  });
});

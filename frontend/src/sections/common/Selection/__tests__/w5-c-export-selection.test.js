// W5 C3: a selection reaches W3 B's export dialogs as the shapes they read
// (a `rows` pill on the view; a `system:row_ids` grid filter), the progress
// line's words, and the results selection text.
import { describe, expect, it } from "vitest";
import {
  datasetSelectionFilter,
  resultsSelectionView,
  selectedRowsText,
} from "../exportSelection";
import { progressPercent, progressText } from "../bulkProgress";
import { selectionText } from "../useSelectAllMatching";
import {
  pillLabel,
  rowIdsFromPills,
} from "src/sections/common/Results/filterPills";

describe("w5-c export selection", () => {
  const view = {
    runIds: ["r1", "r2"],
    baselineId: "r1",
    pills: [
      { type: "split", values: ["smoke"] },
      { type: "rows", ids: ["a", "b", "c"], label: "Fixed rows" },
    ],
    sort: "regressed",
  };

  it("narrows the results view to the selection with one rows pill", () => {
    const out = resultsSelectionView(view, ["b", "c", "b", null]);
    expect(out.runIds).toEqual(["r1", "r2"]);
    expect(out.sort).toBe("regressed");
    expect(out.pills.filter((p) => p.type === "rows")).toHaveLength(1);
    expect(out.pills[0]).toEqual({ type: "split", values: ["smoke"] });
    expect(rowIdsFromPills(out.pills)).toEqual(["b", "c"]);
    expect(pillLabel(out.pills[1])).toBe("Selected rows (2)");
    // The page's view is not mutated.
    expect(view.pills).toHaveLength(2);
  });

  it("gives the dataset dialog a system:row_ids filter named by its count", () => {
    expect(datasetSelectionFilter(["x", "y", "x", "z"])).toEqual({
      filters: [
        {
          column_id: "system:row_ids",
          filter_config: {
            filter_type: "categorical",
            filter_op: "in",
            filter_value: ["x", "y", "z"],
            label: "3 selected rows",
          },
        },
      ],
      search: "",
      label: "3 selected rows",
    });
    expect(selectedRowsText(1)).toBe("1 selected row");
    expect(selectedRowsText(1200)).toBe("1,200 selected rows");
  });
});

describe("w5-c bulk progress line", () => {
  const words = { doing: "Exporting", done: "Exported" };
  it("counts against the total while it runs, then says it is done", () => {
    expect(progressText({ ...words, count: 1200, total: 4800 })).toBe(
      "Exporting 1,200 of 4,800 rows…",
    );
    expect(progressText({ ...words, count: 4800, total: 4800 })).toBe(
      "Exported 4,800 rows",
    );
    expect(progressText({ ...words, count: 1, total: 1 })).toBe(
      "Exported 1 row",
    );
  });
  it("never invents a total it was not given", () => {
    expect(progressText({ ...words, count: 300 })).toBe("Exporting 300 rows…");
    expect(progressPercent(300, undefined)).toBeNull();
    expect(progressPercent(0, 0)).toBeNull();
    expect(progressPercent(1200, 4800)).toBe(25);
    expect(progressPercent(5000, 4800)).toBe(100);
  });
});

describe("w5-c results selection text", () => {
  it("names the whole view once every matching row is selected", () => {
    expect(selectionText({ count: 12, total: 34 })).toEqual({
      text: "12 selected",
      title: null,
    });
    expect(selectionText({ count: 34, total: 34 })).toEqual({
      text: "All 34 selected",
      title: "All 34 matching rows selected",
    });
    expect(selectionText({ count: 4800, total: 4800, widened: true })).toEqual({
      text: "All 4,800 selected",
      title: "All 4,800 matching rows selected",
    });
    expect(selectionText({ count: 3 }).text).toBe("3 selected");
    expect(selectionText({ count: 0, total: 0 }).text).toBe("0 selected");
  });
});

import { describe, expect, it } from "vitest";
import { datasetListColumns } from "../datasetListColumns";

describe("LC-04 Rows heading reserves space for the label and sort controls", () => {
  it.each([true, false])("keeps the Rows label readable (real=%s)", (real) => {
    const rows = datasetListColumns({ query: "", rowActions: {}, real }).find(
      (column) => column.id === "numberOfDatapoints",
    );
    // 4-letter Inter label + two 16px icon slots + table cell padding.
    // 76px clips in Firefox even before the sort arrow becomes visible.
    expect(rows.size).toBeGreaterThanOrEqual(112);
    expect(rows.header).toBe("Rows");
    expect(rows.headerAlign).toBe("right");
    expect(rows.align).toBe("right");
  });
});

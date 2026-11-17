// HO D4: the Data grid's optional Row info columns (Comments, Assigned to,
// Tags): offered by the catalog, off by default, values from the reads.
import { describe, expect, it } from "vitest";
import {
  COLLAB_COLUMN,
  assigneesByRow,
  collabCatalogEntries,
  commentsByRow,
  commentsCellText,
  datasetHasTags,
  rowTagNames,
  tagRecordsFor,
} from "../collabColumns";
import { collabSystemColumns } from "../collabColumnDefs";
import { dataGridCatalog } from "../useDataGridLayout";

describe("HO D4 · optional Row info columns", () => {
  it("offers Comments and Assigned to where served, Tags when a row has tags", () => {
    expect(collabCatalogEntries({}).map((e) => e.id)).toEqual([]);
    expect(
      collabCatalogEntries({ served: true, hasTags: true }).map((e) => [
        e.id,
        e.label,
        e.defaultVisible,
        e.group,
      ]),
    ).toEqual([
      [COLLAB_COLUMN.comments, "Comments", false, "system"],
      [COLLAB_COLUMN.assigned, "Assigned to", false, "system"],
      [COLLAB_COLUMN.tags, "Tags", false, "system"],
    ]);
    expect(datasetHasTags({ counts: { tags: { vip: 2 } } })).toBe(true);
    expect(datasetHasTags({ counts: { tags: {} } })).toBe(false);
    // The catalog keeps its own order and appends them after Updated.
    const ids = dataGridCatalog([{ id: "c1", name: "q", role: "input" }], {
      collab: collabCatalogEntries({ served: true }),
    }).map((c) => c.id);
    expect(ids.slice(-3)).toEqual([
      "system:updated",
      COLLAB_COLUMN.comments,
      COLLAB_COLUMN.assigned,
    ]);
  });

  it("reads comments per row, open assignments per row, and row tags", () => {
    const byRow = commentsByRow({
      rows: [
        { row_id: "r1", open: 2, resolved: 1, total: 3 },
        { row_id: "r2", open: 0, resolved: 1, total: 1 },
      ],
    });
    expect(commentsCellText(byRow.r1)).toBe("2 open · 1 resolved");
    expect(commentsCellText(byRow.r2)).toBe("1 resolved");
    expect(commentsCellText(byRow.r3)).toBe("");
    expect(
      assigneesByRow({
        assignments: [
          { row_id: "r1", assignee: { name: "Maya Chen" }, status: "open" },
          { row_id: "r1", assignee: { name: "Maya Chen" }, status: "open" },
          { row_id: "r1", assignee: { name: "Ravi Menon" }, status: "open" },
          { row_id: "r2", assignee: { name: "Asha Rao" }, status: "done" },
          { row_id: "r3", run_id: "x", assignee: { name: "Asha Rao" } },
        ],
      }),
    ).toEqual({ r1: ["Maya Chen", "Ravi Menon"] });
    expect(
      rowTagNames({ row_meta: { metadata: { tags: ["vip", ""] } } }),
    ).toEqual(["vip"]);
    expect(
      tagRecordsFor(["VIP", "new"], [{ id: "t1", name: "vip", color: "teal" }]),
    ).toEqual([
      { id: "t1", name: "vip", color: "teal" },
      { id: "name:new", name: "new" },
    ]);
  });

  it("makes a grid column per offered id with the grid's factory", () => {
    const system = (field, headerName, extra) => ({
      field,
      headerName,
      ...extra,
    });
    const defs = collabSystemColumns(system, [
      COLLAB_COLUMN.tags,
      "system:other",
      COLLAB_COLUMN.comments,
    ]);
    expect(defs.map((d) => [d.field, d.headerName])).toEqual([
      [COLLAB_COLUMN.tags, "Tags"],
      [COLLAB_COLUMN.comments, "Comments"],
    ]);
    expect(defs.every((d) => typeof d.cellRenderer === "function")).toBe(true);
  });
});

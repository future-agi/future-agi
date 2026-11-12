import { beforeEach, describe, expect, it } from "vitest";
import {
  VERSION_CELL_CLASS,
  VERSION_ROW_DELETED_CLASS,
  currentTableView,
  isUserDataColumn,
  tableRequestFor,
  useAsOfViewStore,
  versionCellClassRules,
  versionCellTooltip,
  versionRowClassRules,
} from "../versionedSource";

describe("tableRequestFor", () => {
  it("reads latest from get-dataset-table", () => {
    expect(
      tableRequestFor({ datasetId: "ds1", params: { page_size: 30 } }),
    ).toEqual({
      url: "/model-hub/develops/ds1/get-dataset-table/",
      params: { page_size: 30 },
      asOf: false,
    });
  });

  it("reads a past version from the N3 as-of table", () => {
    expect(
      tableRequestFor({ datasetId: "ds1", versionNumber: 5, versionId: "v5" }),
    ).toEqual({
      url: "/model-hub/datasets/ds1/versions/v5/table/",
      params: {},
      asOf: true,
    });
  });

  it("stays on latest until the version id is known", () => {
    expect(tableRequestFor({ datasetId: "ds1", versionNumber: 5 }).asOf).toBe(
      false,
    );
  });
});

describe("tableRequestFor narrowing (AsOfBanner chips)", () => {
  it("passes compare/change to N3", () => {
    expect(
      tableRequestFor({
        datasetId: "ds1",
        versionNumber: 5,
        versionId: "v5",
        compare: "in",
        change: "deleted",
        params: { page_size: 30 },
      }),
    ).toEqual({
      url: "/model-hub/datasets/ds1/versions/v5/table/",
      params: { page_size: 30, compare: "in", change: "deleted" },
      asOf: true,
    });
  });

  it("marks 'added since' as a latest view", () => {
    const req = tableRequestFor({
      datasetId: "ds1",
      versionNumber: 5,
      versionId: "v5",
      compare: "since",
      change: "added",
    });
    expect(req.latestView).toBe(true);
    expect(req.params).toEqual({ compare: "since", change: "added" });
  });
});

describe("currentTableView", () => {
  beforeEach(() => useAsOfViewStore.getState().resetView("ds1"));

  it("is null on plain latest", () => {
    expect(currentTableView("ds1", null)).toBeNull();
  });

  it("reads the version and the chip state", () => {
    useAsOfViewStore.getState().setView("ds1", { change: "modified" });
    expect(currentTableView("ds1", 5)).toEqual({
      versionNumber: 5,
      compare: "since",
      change: "modified",
    });
  });

  it("turns 'added since' into an N3 read on latest", () => {
    useAsOfViewStore.getState().setView("ds1", { addedSince: 5 });
    expect(currentTableView("ds1", null)).toEqual({
      versionNumber: 5,
      compare: "since",
      change: "added",
    });
  });
});

describe("grid marks", () => {
  const params = (change, field = "C3") => ({
    data: { row_meta: { change } },
    colDef: { field },
  });

  it("outlines only the changed cells", () => {
    const change = {
      kind: "modified",
      against: 6,
      changed_column_ids: ["C3"],
      latest_values: { C3: "For Enterprise workspaces" },
    };
    expect(versionCellClassRules[VERSION_CELL_CLASS](params(change))).toBe(
      true,
    );
    expect(
      versionCellClassRules[VERSION_CELL_CLASS](params(change, "C1")),
    ).toBe(false);
    expect(versionCellTooltip(params(change))).toBe(
      "Latest (v6): “For Enterprise workspaces”",
    );
    expect(versionCellTooltip(params(change, "C1"))).toBeUndefined();
  });

  it("shows the value before this version in 'In this version'", () => {
    const change = {
      kind: "modified",
      against: 4,
      changed_column_ids: ["C3"],
      previous_values: { C3: "" },
    };
    expect(versionCellTooltip(params(change))).toBe("Before (v4): empty");
  });

  it("strikes through deleted rows", () => {
    const rule = versionRowClassRules[VERSION_ROW_DELETED_CLASS];
    expect(rule(params({ kind: "deleted_later" }))).toBe(true);
    expect(rule(params({ kind: "deleted", version: 5 }))).toBe(true);
    expect(rule(params(null))).toBe(false);
    expect(versionCellTooltip(params({ kind: "deleted", version: 5 }))).toBe(
      "Deleted in v5",
    );
  });

  it("keeps user-data columns and drops generated ones", () => {
    expect(isUserDataColumn({ origin_type: "OTHERS" })).toBe(true);
    expect(isUserDataColumn({ origin_type: "run_prompt" })).toBe(false);
    expect(isUserDataColumn({ originType: "evaluation" })).toBe(false);
  });
});

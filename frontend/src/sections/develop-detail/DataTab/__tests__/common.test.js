import { describe, it, expect, vi } from "vitest";
import {
  enhanceCol,
  getColumnConfig,
  RefreshStatus,
  withPendingColumnLoadingState,
} from "../common";
import { StatusTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

describe("enhanceCol", () => {
  it("preserves null data_type from snake_case input", () => {
    const avgMeta = [{ id: "col1", metadata: {} }];
    const result = enhanceCol({ id: "col1", data_type: null }, avgMeta);
    expect(result.data_type).toBe(null);
    expect(result.dataType).toBe(null);
  });

  it("returns col unchanged when no metadata match found", () => {
    const col = { id: "col1", data_type: "text" };
    expect(enhanceCol(col, [])).toBe(col);
  });

  it("sets metadata from matching averageMetaData entry", () => {
    const avgMeta = [{ id: "col1", metadata: { min: 0, max: 100 } }];
    const result = enhanceCol({ id: "col1", data_type: "integer" }, avgMeta);
    expect(result.metadata).toEqual({ min: 0, max: 100 });
  });

  it("copies snake_case values to both shapes", () => {
    const avgMeta = [{ id: "col1", metadata: {} }];
    const col = {
      id: "col1",
      data_type: "text",
      origin_type: "dataset",
      is_frozen: true,
      is_visible: false,
      source_id: "src-1",
    };
    const result = enhanceCol(col, avgMeta);
    expect(result.data_type).toBe("text");
    expect(result.dataType).toBe("text");
    expect(result.origin_type).toBe("dataset");
    expect(result.originType).toBe("dataset");
    expect(result.is_frozen).toBe(true);
    expect(result.isFrozen).toBe(true);
    expect(result.is_visible).toBe(false);
    expect(result.isVisible).toBe(false);
    expect(result.source_id).toBe("src-1");
    expect(result.sourceId).toBe("src-1");
  });
});

describe("getColumnConfig valueGetter", () => {
  const mkConfig = (overrides = {}) =>
    getColumnConfig({
      eachCol: { id: "col1", name: "Col 1", data_type: "text" },
      children: undefined,
      queryClient: { invalidateQueries: vi.fn() },
      dataset: "test-ds",
      getWaveSurferInstance: vi.fn(),
      storeWaveSurferInstance: vi.fn(),
      removeWaveSurferInstance: vi.fn(),
      updateWaveSurferInstance: vi.fn(),
      ...overrides,
    });

  it("returns null when cell_value is null", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: { cell_value: null } },
    });
    expect(result).toBe(null);
  });

  it("returns parsed value when cell_value is a string", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: { cell_value: "hello" } },
    });
    expect(result).toBe("hello");
  });

  it("returns undefined when cell_value is missing", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: { col1: {} },
    });
    expect(result).toBe(undefined);
  });

  it("returns undefined when cell is absent", () => {
    const config = mkConfig();
    const result = config.valueGetter({
      data: {},
    });
    expect(result).toBe(undefined);
  });
});

describe("withPendingColumnLoadingState", () => {
  // A just-added eval-prompt/dynamic column: column status is a refreshing
  // value while the async worker has not yet flipped the cells to "running".
  const refreshingConfig = [
    { id: "colDone", status: "Completed" },
    { id: "colPending", status: "NotStarted" },
  ];
  const mkRows = () => [
    { rowId: 1, colDone: { cell_value: "answer", status: "pass" } },
    { rowId: 2, colDone: { cell_value: "answer", status: "pass" } },
  ];

  it("marks blank cells of a refreshing column as running so they load", () => {
    const rows = mkRows();
    const result = withPendingColumnLoadingState(rows, refreshingConfig);
    // The pending column had no cell at all in the row -> now a running cell.
    expect(result[0].colPending.status).toBe(StatusTypes.RUNNING);
    expect(result[1].colPending.status).toBe(StatusTypes.RUNNING);
  });

  it("NotStarted is one of the statuses that triggers loading", () => {
    // Guards against the constant drifting away from the render behaviour.
    expect(RefreshStatus).toContain("NotStarted");
    expect(RefreshStatus).toContain("Running");
  });

  it("leaves cells that already have a value untouched", () => {
    const result = withPendingColumnLoadingState(mkRows(), refreshingConfig);
    expect(result[0].colDone.cell_value).toBe("answer");
    expect(result[0].colDone.status).toBe("pass");
  });

  it("does not mark cells when the column has finished (Completed)", () => {
    const rows = [{ rowId: 1, colDone: { cell_value: "", status: "pass" } }];
    const config = [{ id: "colDone", status: "Completed" }];
    const result = withPendingColumnLoadingState(rows, config);
    expect(result[0].colDone.status).toBe("pass");
  });

  it("preserves an existing error status on a refreshing column", () => {
    const rows = [{ rowId: 1, colPending: { status: "error" } }];
    const config = [{ id: "colPending", status: "Running" }];
    const result = withPendingColumnLoadingState(rows, config);
    expect(result[0].colPending.status).toBe("error");
  });

  it("returns rows unchanged when no column is refreshing", () => {
    const rows = mkRows();
    const config = [{ id: "colDone", status: "Completed" }];
    expect(withPendingColumnLoadingState(rows, config)).toBe(rows);
  });
});

// L1 (the capability contract): on the real backend the dataset grid writes at
// once through the endpoints dev has (update_cell_value, delete_row,
// duplicate-rows, clone-dataset), with bodies dev's serializers read, and
// nothing is staged as a pending change no one can save. The cell body is
// checked against dev's strict DatasetUpdateCellValueRequestSerializer,
// read from the backend source.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn(() => Promise.resolve({ data: { status: true } }));
const del = vi.fn(() => Promise.resolve({ data: { status: true } }));
vi.mock("src/utils/axios", () => ({
  default: {
    post: (...a) => post(...a),
    delete: (...a) => del(...a),
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      updateCellValue: (id) => `/model-hub/develops/${id}/update_cell_value/`,
      deleteDatasetRow: (id) => `/model-hub/develops/${id}/delete_row/`,
      duplicateDatasetRows: (id) => `/model-hub/datasets/${id}/duplicate-rows/`,
      cloneDataset: (id) => `/model-hub/develops/clone-dataset/${id}/`,
    },
  },
}));
const snack = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => snack(...a),
}));

import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { NOT_SAVED_NO_VERSIONS, useChangeSetStore } from "../changeSetStore";
import { versionedCellEdit } from "../versionedCellEdit";
import {
  cloneDatasetClassic,
  deleteRowsClassic,
  duplicateRowsClassic,
  refusalSentence,
  writeCellClassic,
} from "../classicWrites";
import { cellWriter } from "../../common";

const DS = "ds-l1";
const CONTRACTS = path.resolve(
  process.cwd(),
  "../futureagi/model_hub/serializers/contracts.py",
);
function serializerFields(className) {
  const src = fs.readFileSync(CONTRACTS, "utf8");
  const start = src.indexOf(`class ${className}(`);
  expect(start).toBeGreaterThan(-1);
  const rest = src.slice(start + 1);
  const body = rest.slice(0, rest.search(/\nclass /));
  return [...body.matchAll(/^ {4}(\w+) = /gm)].map((m) => m[1]);
}

const event = (newValue, oldValue, extra = {}) => ({
  type: "cellValueChanged",
  source: "edit",
  newValue,
  oldValue,
  data: { row_id: "ROW_1" },
  column: { colId: "COL_1", colDef: { dataType: "text", headerName: "q" } },
  ...extra,
});

beforeEach(() => {
  setRealBackendForTests(true);
  useChangeSetStore.setState({ sets: {} });
});
afterEach(() => {
  setRealBackendForTests(null);
  post.mockClear();
  del.mockClear();
  snack.mockClear();
});

describe("L1 grid edits on the real backend", () => {
  it("hand every edit to the immediate writer and stage nothing", () => {
    const fallback = vi.fn();
    const edit = versionedCellEdit({
      datasetId: DS,
      fallback,
      mediaPending: true,
    });
    edit(event("Fixed", "old"));
    edit(
      event("data:image/png;base64,AAA", null, {
        column: { colId: "C_IMG", colDef: { dataType: "image" } },
      }),
    );
    expect(fallback).toHaveBeenCalledTimes(2);
    expect(fallback.mock.calls[0][0].newValue).toBe("Fixed");
    expect(useChangeSetStore.getState().sets[DS]).toBeUndefined();
  });

  it("refuse text in a number cell before any request, and put the value back", () => {
    const fallback = vi.fn(
      cellWriter({
        versioned: true,
        queryClient: { invalidateQueries: vi.fn() },
        dataset: DS,
      }),
    );
    const setDataValue = vi.fn();
    const refreshCells = vi.fn();
    // The grid's valueSetter has already put the refused text in the cell.
    const data = {
      row_id: "ROW_1",
      C_NUM: { cell_id: "CELL_NUM", cell_value: "ten" },
    };
    const node = { data, setDataValue };
    versionedCellEdit({ datasetId: DS, fallback })(
      event("ten", 3, {
        data,
        column: {
          colId: "C_NUM",
          colDef: { dataType: "integer", headerName: "count" },
        },
        node,
        api: { getRowNode: () => node, refreshCells },
      }),
    );
    expect(post).not.toHaveBeenCalled();
    expect(fallback).not.toHaveBeenCalled();
    expect(node.data.C_NUM).toEqual({ cell_id: "CELL_NUM", cell_value: 3 });
    expect(refreshCells).toHaveBeenCalledTimes(1);
    expect(refreshCells).toHaveBeenCalledWith({
      rowNodes: [node],
      columns: ["C_NUM"],
      force: true,
    });
    expect(setDataValue).not.toHaveBeenCalled();
    expect(snack).toHaveBeenCalledWith(
      expect.stringMatching(/^count: /),
      expect.objectContaining({ variant: "error" }),
    );
  });

  it("ignore grid-internal value changes (no source)", () => {
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback })({
      ...event("x", "y"),
      source: undefined,
    });
    expect(fallback).not.toHaveBeenCalled();
  });

  it("make the column's own writer the immediate update_cell_value", () => {
    const setDataValue = vi.fn();
    const write = cellWriter({
      versioned: true,
      queryClient: { invalidateQueries: vi.fn() },
      dataset: DS,
    });
    write({
      ...event("New text", "old"),
      api: { getRowNode: () => ({ setDataValue }) },
    });
    expect(post).toHaveBeenCalledTimes(1);
    const [url, body] = post.mock.calls[0];
    expect(url).toBe(`/model-hub/develops/${DS}/update_cell_value/`);
    expect(body).toEqual({
      column_id: "COL_1",
      row_id: "ROW_1",
      new_value: "New text",
    });
    const allowed = serializerFields("DatasetUpdateCellValueRequestSerializer");
    for (const key of Object.keys(body)) expect(allowed).toContain(key);
  });

  it("keep the pending change-set in prototype mode", () => {
    setRealBackendForTests(false);
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback })(event("Fixed", "old"));
    expect(fallback).not.toHaveBeenCalled();
    expect(useChangeSetStore.getState().getOps(DS)).toHaveLength(1);
  });
});

describe("L1 the change-set refuses out loud on the real backend", () => {
  it("never holds a change it cannot save", () => {
    const kept = useChangeSetStore
      .getState()
      .addOp(DS, { op: "add_row", temp_id: "new-1", cells: {} });
    expect(kept).toBe(false);
    expect(useChangeSetStore.getState().getOps(DS)).toHaveLength(0);
    expect(snack).toHaveBeenCalledWith(
      NOT_SAVED_NO_VERSIONS,
      expect.objectContaining({ variant: "error" }),
    );
  });
});

describe("L1 classic writes send dev's bodies", () => {
  it("cell, delete, duplicate and clone", async () => {
    await writeCellClassic({
      datasetId: DS,
      rowId: "R",
      columnId: "C",
      value: { a: 1 },
    });
    expect(post.mock.calls[0]).toEqual([
      `/model-hub/develops/${DS}/update_cell_value/`,
      { row_id: "R", column_id: "C", new_value: '{"a":1}' },
    ]);
    await deleteRowsClassic({ datasetId: DS, rowIds: ["R1", "R2"] });
    expect(del.mock.calls[0]).toEqual([
      `/model-hub/develops/${DS}/delete_row/`,
      { data: { row_ids: ["R1", "R2"], selected_all_rows: false } },
    ]);
    await duplicateRowsClassic({ datasetId: DS, rowIds: ["R1"], copies: "2" });
    expect(post.mock.calls[1]).toEqual([
      `/model-hub/datasets/${DS}/duplicate-rows/`,
      { row_ids: ["R1"], num_copies: 2, selected_all_rows: false },
    ]);
    const dup = serializerFields("DuplicateRowsRequestSerializer");
    const sel = serializerFields("DatasetRowSelectionRequestSerializer");
    for (const key of Object.keys(post.mock.calls[1][1]))
      expect([...dup, ...sel]).toContain(key);
    // No `pending`: dev's serializer does not have it.
    expect("pending" in post.mock.calls[1][1]).toBe(false);
    await cloneDatasetClassic(DS);
    expect(post.mock.calls[2]).toEqual([
      `/model-hub/develops/clone-dataset/${DS}/`,
      {},
    ]);
    // No `dry_run` field anywhere: dev's clone would make a real copy.
    expect(JSON.stringify(post.mock.calls)).not.toMatch(/dry_run|pending/);
  });

  it("say why a write was refused", () => {
    expect(
      refusalSentence({ result: "Some row IDs were not found" }, "x"),
    ).toBe("Some row IDs were not found");
    expect(
      refusalSentence({ message: "Request failed with status 500" }, "Nope"),
    ).toBe("Nope");
    expect(refusalSentence(undefined, "Nope")).toBe("Nope");
  });
});

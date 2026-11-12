import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { SnackbarProvider } from "notistack";
import {
  AllCommunityModule,
  ModuleRegistry,
  createGrid,
} from "ag-grid-community";
import axios, { endpoints } from "src/utils/axios";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { getColumnConfig, onCellValueChangedWrapper } from "../common";
import { versionedCellEdit } from "../Versions/versionedCellEdit";

ModuleRegistry.registerModules([AllCommunityModule]);

// Commits overlap locally, but requests for the same dataset/row/column must
// not: the backend saves in arrival order, without an edit-order token.
// This HTTP stub holds each POST before persistence, then saves its payload
// only on success. GET returns fresh row objects from that durable store,
// never the grid's optimistic data. Only HTTP and jsdom layout are stubbed;
// editing, Tab navigation, the getter/setter and the writer use real AG Grid.
const DATASET = "cell-overlap-dataset";
const OTHER_DATASET = "cell-overlap-other-dataset";
const ROW = "cell-overlap-row";
const OTHER_ROW = "cell-overlap-other-row";
const COLUMN = "edited";
const NEXT = "next";
const OBSERVATION_MS = 2100;
let api;
let host;
let post;
let pending;
let tables;
let currentDataset;

function column(id, dataset) {
  const config = getColumnConfig({
    eachCol: {
      id,
      name: id,
      data_type: "text",
      origin_type: "dataset",
      is_visible: true,
    },
    queryClient: { invalidateQueries: vi.fn() },
    dataset,
    versioned: true,
  });
  // Presentation-only React renderers/menus are outside this minimal grid.
  const {
    valueGetter,
    valueSetter,
    cellEditor,
    cellEditorParams,
    cellEditorPopup,
  } = config;
  return {
    field: id,
    headerName: id,
    dataType: "text",
    editable: true,
    cellDataType: false,
    valueGetter,
    valueSetter,
    cellEditor,
    cellEditorParams,
    cellEditorPopup,
  };
}

async function readTable(dataset = DATASET) {
  const { data } = await axios.get(endpoints.develop.getDatasetDetail(dataset));
  return data.result.table;
}

async function mountGrid(dataset = DATASET) {
  api?.destroy();
  host?.remove();
  currentDataset = dataset;
  const rowData = await readTable(dataset);
  host = document.createElement("div");
  host.style.cssText = "width:800px;height:400px";
  document.body.append(host);
  api = createGrid(host, {
    columnDefs: [column(COLUMN, dataset), column(NEXT, dataset)],
    rowData,
    getRowId: ({ data }) => data.row_id,
    onCellValueChanged: versionedCellEdit({
      datasetId: dataset,
      fallback: onCellValueChangedWrapper(
        { invalidateQueries: vi.fn() },
        dataset,
      ),
      mediaPending: true,
    }),
    stopEditingWhenCellsLoseFocus: true,
    suppressColumnVirtualisation: true,
    suppressRowVirtualisation: true,
    animateRows: false,
  });
  await waitFor(() => expect(editedCell()).not.toBeNull());
}

const editedCell = (columnId = COLUMN, rowId = ROW) =>
  host.querySelector(`[row-id="${rowId}"] [col-id="${columnId}"]`);
const editing = () => api.getEditingCells().map((c) => c.column.getColId());

// Tab opens the next cell's editor; Enter leaves no editor open. A commit
// updates display immediately, even when its HTTP request must wait.
async function edit(value, key, columnId = COLUMN, rowId = ROW) {
  if (editing().length) api.stopEditing(true);
  // A real double click's mouse-down focuses the cell; jsdom's touch support
  // makes AG Grid skip mouse-down, so focus it as that mouse-down would.
  api.setFocusedCell(api.getRowNode(rowId).rowIndex, columnId);
  fireEvent.doubleClick(editedCell(columnId, rowId), { detail: 2 });
  await waitFor(() =>
    expect(document.querySelector(".ag-popup-editor textarea")).not.toBeNull(),
  );
  const input = document.querySelector(".ag-popup-editor textarea");
  input.focus();
  fireEvent.input(input, { target: { value } });
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, {
    key,
    code: key,
    keyCode: key === "Tab" ? 9 : 13,
    which: key === "Tab" ? 9 : 13,
  });
  await waitFor(() => expect(editing()).toEqual(key === "Tab" ? [NEXT] : []));
  await expectShown(value, columnId, rowId);
}

const afterResponse = () => new Promise((resolve) => setTimeout(resolve, 50));

async function expectShown(value, columnId = COLUMN, rowId = ROW) {
  expect(api.getRowNode(rowId).data[columnId].cell_value).toBe(value);
  await waitFor(() => expect(editedCell(columnId, rowId).textContent).toBe(value));
}

async function expectPersisted(
  value,
  dataset = DATASET,
  columnId = COLUMN,
  rowId = ROW,
) {
  const rows = await readTable(dataset);
  expect(rows.find((row) => row.row_id === rowId)[columnId].cell_value).toBe(value);
}

function expectRequest(index, value, dataset = DATASET, columnId = COLUMN, rowId = ROW) {
  expect(post.mock.calls[index]).toEqual([
    endpoints.develop.updateCellValue(dataset),
    { column_id: columnId, row_id: rowId, new_value: value },
  ]);
}

// RED on the unsequenced writer: its second POST starts while the first is
// still held. Do not wait for a second POST before checking this invariant.
async function twoQueuedSaves(key) {
  await mountGrid();
  await edit("first value", key);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await edit("second value", key);
  // Allow the real grid's asynchronous cellValueChanged event to dispatch.
  await afterResponse();
  expect(post).toHaveBeenCalledTimes(1);
  expectRequest(0, "first value");
  await expectShown("second value");
  await expectPersisted("before");
}

// Real elapsed time after the last response: an echo would be sent here,
// change which editor is open, or enqueue an unwanted third write.
async function holdAfterLastResponse(key, writes = 2) {
  const open = key === "Tab" ? [NEXT] : [];
  expect(editing()).toEqual(open);
  await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
  expect(editing()).toEqual(open);
  expect(post).toHaveBeenCalledTimes(writes);
}

async function expectReloaded(value) {
  await expectPersisted(value, currentDataset);
  await mountGrid(currentDataset);
  await expectShown(value);
}

beforeEach(() => {
  render(<SnackbarProvider />);
  setRealBackendForTests(true);
  pending = [];
  tables = new Map(
    [DATASET, OTHER_DATASET].map((dataset) => [
      dataset,
      [
        {
          row_id: ROW,
          [COLUMN]: { cell_value: "before" },
          [NEXT]: { cell_value: "next unchanged" },
        },
        {
          row_id: OTHER_ROW,
          [COLUMN]: { cell_value: "other before" },
          [NEXT]: { cell_value: "other next unchanged" },
        },
      ],
    ]),
  );
  vi.spyOn(axios, "get").mockImplementation(async (url) => {
    const dataset = [...tables.keys()].find(
      (id) => endpoints.develop.getDatasetDetail(id) === url,
    );
    if (!dataset) throw new Error(`Unexpected GET: ${url}`);
    return { data: { result: { table: JSON.parse(JSON.stringify(tables.get(dataset))) } } };
  });
  post = vi.spyOn(axios, "post").mockImplementation((url, payload) => {
    const dataset = [...tables.keys()].find(
      (id) => endpoints.develop.updateCellValue(id) === url,
    );
    if (!dataset) throw new Error(`Unexpected POST: ${url}`);
    const { row_id: rowId, column_id: columnId, new_value: value } = payload;
    return new Promise((resolve, reject) => {
      let settled = false;
      pending.push({
        resolve: () => {
          if (settled) return;
          settled = true;
          const row = tables.get(dataset).find((item) => item.row_id === rowId);
          row[columnId].cell_value = value;
          resolve({ data: {} });
        },
        reject: (error) => {
          if (settled) return;
          settled = true;
          reject(error);
        },
      });
    });
  });
  // jsdom has no layout. These shims do not implement editing behavior.
  vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockImplementation(
    function () {
      return this.parentElement;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
  // PopupService closes a popup whose anchor rectangle is all zeroes.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 800,
    bottom: 400,
    width: 800,
    height: 400,
    toJSON: () => ({}),
  });
});

afterEach(async () => {
  api?.destroy();
  api = undefined;
  host?.remove();
  // Drain even after an assertion fails, including requests released by a
  // predecessor, so a failed test cannot leave the module's cell queue held.
  for (let index = 0; index < pending.length; index += 1) {
    pending[index].resolve();
    await afterResponse();
  }
  cleanup();
  setRealBackendForTests(null);
  vi.restoreAllMocks();
});

describe.each(["Tab", "Enter"])(
  "overlapping commits of one cell with %s, real AG Grid",
  (key) => {
    it.each([
      { first: "success", second: "success", final: "second value" },
      { first: "failure", second: "success", final: "second value" },
      { first: "success", second: "failure", final: "first value" },
      { first: "failure", second: "failure", final: "before" },
    ])(
      "first $first, second $second: serialize persistence and reload $final without an echo",
      async ({ first, second, final }) => {
        await twoQueuedSaves(key);
        if (first === "success") pending[0].resolve();
        else pending[0].reject(new Error("first save failed"));
        await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
        expectRequest(1, "second value");
        // Settling an older write must not roll back the newer pending edit.
        await expectShown("second value");
        await expectPersisted(first === "success" ? "first value" : "before");
        if (second === "success") pending[1].resolve();
        else pending[1].reject(new Error("second save failed"));
        await afterResponse();
        await expectShown(final);
        await holdAfterLastResponse(key);
        await expectReloaded(final);
        expect(post).toHaveBeenCalledTimes(2);
      },
    );
  },
);

describe("cell-write queue identity and lifetime", () => {
  it.each([
    { scope: "column", dataset: DATASET, columnId: NEXT, rowId: ROW },
    { scope: "row", dataset: DATASET, columnId: COLUMN, rowId: OTHER_ROW },
    { scope: "dataset", dataset: OTHER_DATASET, columnId: COLUMN, rowId: ROW },
  ])(
    "keeps a different $scope parallel while the first request is held",
    async ({ dataset, columnId, rowId }) => {
      await mountGrid();
      await edit("first value", "Enter");
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      if (dataset !== DATASET) await mountGrid(dataset);
      await edit("independent value", "Enter", columnId, rowId);
      await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
      expectRequest(1, "independent value", dataset, columnId, rowId);
      // The unrelated request can persist before the held one.
      pending[1].resolve();
      await afterResponse();
      await expectPersisted("independent value", dataset, columnId, rowId);
      await expectShown("independent value", columnId, rowId);
      await expectPersisted("before");
      pending[0].resolve();
      await afterResponse();
      await expectPersisted("first value");
      expect(post).toHaveBeenCalledTimes(2);
    },
  );

  it("sequences the same cell across replacement row objects and writer instances", async () => {
    await mountGrid();
    await edit("first value", "Enter");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    // Refresh/remount while the first write is pending. A cell-object or
    // wrapper-local queue would incorrectly allow this second request out.
    await mountGrid();
    await edit("second value", "Enter");
    await afterResponse();
    expect(post).toHaveBeenCalledTimes(1);
    pending[0].resolve();
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expectRequest(1, "second value");
    pending[1].resolve();
    await afterResponse();
    await expectShown("second value");
    await expectReloaded("second value");
  });

  it("does not release a newer queue tail when an older request finishes", async () => {
    await twoQueuedSaves("Enter");
    pending[0].resolve();
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    await edit("third value", "Enter");
    await afterResponse();
    expect(post).toHaveBeenCalledTimes(2);
    pending[1].reject(new Error("second save failed"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    expectRequest(2, "third value");
    await expectShown("third value");
    pending[2].resolve();
    await afterResponse();
    await holdAfterLastResponse("Enter", 3);
    await expectReloaded("third value");
  });
});

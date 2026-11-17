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

const DATASET = "cell-echo-dataset";
const ROW = "cell-echo-row";
const COLUMN = "edited";
const NEXT = "next";
const OBSERVATION_MS = 2100;
let api;
let host;
let post;
let pending;

// This uses the installed AG Grid 35.0.0, including its EditService, popup
// editors, Tab navigation and the application's actual getter/valueSetter.
// Only HTTP is stubbed; no grid API, row node or event is mocked.
function column(id, dataType) {
  const config = getColumnConfig({
    eachCol: {
      id,
      name: id,
      data_type: dataType,
      origin_type: "dataset",
      is_visible: true,
    },
    queryClient: { invalidateQueries: vi.fn() },
    dataset: DATASET,
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
    dataType,
    editable: true,
    cellDataType: false,
    valueGetter,
    valueSetter,
    cellEditor,
    cellEditorParams,
    cellEditorPopup,
  };
}

async function mountGrid(dataType = "text", initial = "before") {
  host = document.createElement("div");
  host.style.cssText = "width:800px;height:400px";
  document.body.append(host);
  api = createGrid(host, {
    columnDefs: [column(COLUMN, dataType), column(NEXT, "text")],
    rowData: [
      {
        row_id: ROW,
        [COLUMN]: { cell_value: initial },
        [NEXT]: { cell_value: "next unchanged" },
      },
    ],
    getRowId: ({ data }) => data.row_id,
    onCellValueChanged: versionedCellEdit({
      datasetId: DATASET,
      fallback: onCellValueChangedWrapper(
        { invalidateQueries: vi.fn() },
        DATASET,
      ),
      mediaPending: true,
    }),
    stopEditingWhenCellsLoseFocus: true,
    suppressColumnVirtualisation: true,
    suppressRowVirtualisation: true,
    animateRows: false,
  });
  await waitFor(() =>
    expect(
      host.querySelector(`[row-id="${ROW}"] [col-id="${COLUMN}"]`),
    ).not.toBeNull(),
  );
}

// A real double click's mouse-down focuses the cell (AG Grid 35
// CellMouseListenerFeature.onMouseDown -> focusCell), and Tab then moves on
// from the focused cell (EditService.moveToNextEditingCell). jsdom reports
// `ontouchstart`, so AG Grid listens for touchstart instead of mousedown and a
// synthetic mouse-down focuses nothing: after a first Tab the focus would stay
// on NEXT. Focus the cell through the grid's API, as the mouse-down would.
function focusLikeMouseDown() {
  api.setFocusedCell(0, COLUMN);
}

// A local commit can navigate before its same-cell request leaves the queue.
async function commitEdit(value, key = "Tab", type = "text") {
  const cell = host.querySelector(`[row-id="${ROW}"] [col-id="${COLUMN}"]`);
  focusLikeMouseDown();
  fireEvent.doubleClick(cell, { detail: 2 });
  const selector =
    type === "datetime"
      ? '.ag-popup-editor input[type="date"]'
      : ".ag-popup-editor textarea";
  await waitFor(() => expect(document.querySelector(selector)).not.toBeNull());
  const input = document.querySelector(selector);
  input.focus();
  fireEvent.input(input, { target: { value } });
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, {
    key,
    code: key,
    keyCode: key === "Tab" ? 9 : 13,
    which: key === "Tab" ? 9 : 13,
  });
  await waitFor(() =>
    expect(api.getEditingCells().map((c) => c.column.getColId())).toEqual(
      key === "Tab" ? [NEXT] : [],
    ),
  );
}

// Single-write cases still wait explicitly for their request to start.
async function edit(value, key = "Tab", type = "text") {
  const writesBefore = post.mock.calls.length;
  await commitEdit(value, key, type);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(writesBefore + 1));
}

const afterResponse = () => new Promise((resolve) => setTimeout(resolve, 50));

async function observeNextEditor() {
  expect(api.getEditingCells().map((c) => c.column.getColId())).toEqual([NEXT]);
  // Real elapsed time, after the response, not a fake timer or an eventual
  // toHaveBeenCalledTimes(1) which could pass before an echo arrives.
  await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
  expect(api.getEditingCells().map((c) => c.column.getColId())).toEqual([NEXT]);
}

// An edit versionedCellEdit refuses (not a number) sends no write of its own;
// wait for the refusal it shows, not for a POST.
async function refusedEdit(value, refusal, key = "Tab", selector) {
  const cell = host.querySelector(`[row-id="${ROW}"] [col-id="${COLUMN}"]`);
  focusLikeMouseDown();
  fireEvent.doubleClick(cell, { detail: 2 });
  await waitFor(() => expect(document.querySelector(selector)).not.toBeNull());
  const input = document.querySelector(selector);
  input.focus();
  fireEvent.input(input, { target: { value } });
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, {
    key,
    code: key,
    keyCode: key === "Tab" ? 9 : 13,
    which: key === "Tab" ? 9 : 13,
  });
  await waitFor(() => expect(document.body.textContent).toContain(refusal));
  await waitFor(() => {
    const editing = api.getEditingCells().map((c) => c.column.getColId());
    if (key === "Tab") expect(editing).toContain(NEXT);
    else expect(editing).toEqual([]);
  });
}

function body(index = 0) {
  expect(post.mock.calls[index][0]).toBe(
    endpoints.develop.updateCellValue(DATASET),
  );
  return post.mock.calls[index][1];
}

beforeEach(() => {
  render(<SnackbarProvider />);
  setRealBackendForTests(true);
  pending = [];
  post = vi
    .spyOn(axios, "post")
    .mockImplementation(
      () => new Promise((resolve, reject) => pending.push({ resolve, reject })),
    );
  // jsdom has no layout. Give the real grid visible cells for keyboard
  // navigation; these shims do not implement any editing behavior.
  vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockImplementation(
    function () {
      return this.parentElement;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
  // PopupService checks the anchor's rectangle every 200 ms and closes a
  // popup whose anchor is all zeroes (jsdom's default), even if it has focus.
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
  // predecessor, before restoring HTTP mocks or reusing this cell's queue.
  for (let index = 0; index < pending.length; index += 1) {
    pending[index].resolve({ data: {} });
    await afterResponse();
  }
  cleanup();
  setRealBackendForTests(null);
  vi.restoreAllMocks();
});

describe("classic dataset acknowledgements with real AG Grid", () => {
  it("Tab commits exactly one POST while the next large-text editor stays open", async () => {
    await mountGrid();
    await edit("tab value");
    expect(body()).toEqual({
      column_id: COLUMN,
      row_id: ROW,
      new_value: "tab value",
    });
    pending[0].resolve({ data: {} });
    await observeNextEditor();
    expect(post).toHaveBeenCalledTimes(1);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("tab value");
  });

  it("Enter is the single-POST control with no next editor", async () => {
    await mountGrid();
    await edit("enter value", "Enter");
    pending[0].resolve({ data: {} });
    await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
    expect(post).toHaveBeenCalledTimes(1);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("enter value");
  });

  it("a failed Tab save reverts locally without writing the rollback", async () => {
    await mountGrid();
    await edit("rejected value");
    pending[0].reject(new Error("synthetic save failure"));
    await observeNextEditor();
    expect(post).toHaveBeenCalledTimes(1);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("before");
  });

  it("a date-editor Tab commit does not write again on acknowledgement", async () => {
    await mountGrid("datetime", new Date(2026, 9, 26));
    await edit("2026-10-27", "Tab", "datetime");
    expect(body()).toEqual({
      column_id: COLUMN,
      row_id: ROW,
      new_value: "2026-10-27 00:00:00",
    });
    pending[0].resolve({ data: {} });
    await observeNextEditor();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("two edits of the same cell queue two writes without restoring the first value", async () => {
    await mountGrid();
    await edit("first value");
    api.stopEditing(true);
    await commitEdit("final value");
    // Let AG Grid dispatch cellValueChanged before checking the queue.
    // RED on the unsequenced writer: POST 2 would already have started.
    await afterResponse();
    expect(post).toHaveBeenCalledTimes(1);
    expect(body().new_value).toBe("first value");
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
    pending[0].resolve({ data: {} });
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(body(1).new_value).toBe("final value");
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
    pending[1].resolve({ data: {} });
    await observeNextEditor();
    expect(post).toHaveBeenCalledTimes(2);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
  });

  it("an older failed save cannot roll back a newer queued edit", async () => {
    await mountGrid();
    await edit("first value");
    api.stopEditing(true);
    await commitEdit("final value");
    await afterResponse();
    expect(post).toHaveBeenCalledTimes(1);
    expect(body().new_value).toBe("first value");
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
    pending[0].reject(new Error("older save failed"));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(body(1).new_value).toBe("final value");
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
    pending[1].resolve({ data: {} });
    await observeNextEditor();
    expect(post).toHaveBeenCalledTimes(2);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("final value");
  });

  it.each(["cell_value", "cellValue"])(
    "a failed dialog save preserves the %s cell shape without a rollback POST",
    async (valueKey) => {
      await mountGrid();
      const node = api.getRowNode(ROW);
      node.data[COLUMN] = { [valueKey]: "before", cell_id: "original-cell" };
      api.startEditingCell({ rowIndex: 0, colKey: NEXT });
      await waitFor(() => expect(api.getEditingCells()).toHaveLength(1));
      onCellValueChangedWrapper(
        { invalidateQueries: vi.fn() },
        DATASET,
      )({
        api,
        node,
        data: node.data,
        column: api.getColumn(COLUMN),
        newValue: "rejected",
        oldValue: "before",
        source: "edit",
      });
      pending[0].reject(new Error("dialog save failed"));
      await observeNextEditor();
      expect(post).toHaveBeenCalledTimes(1);
      expect(node.data[COLUMN]).toEqual({
        [valueKey]: "before",
        cell_id: "original-cell",
      });
    },
  );

  it.each(["json", "image"])(
    "a manual %s dialog save updates display without echoing through an open editor",
    async (dataType) => {
      await mountGrid();
      // Dialog Apply Changes cancels inline editing and calls this same writer
      // directly (JsonCellEditor / media renderers), without a valueSetter commit.
      api.startEditingCell({ rowIndex: 0, colKey: NEXT });
      await waitFor(() => expect(api.getEditingCells()).toHaveLength(1));
      const value =
        dataType === "json"
          ? '{"answer":42}'
          : "data:image/png;base64,aGVsbG8=";
      const col = api.getColumn(COLUMN);
      col.getColDef().dataType = dataType;
      onCellValueChangedWrapper(
        { invalidateQueries: vi.fn() },
        DATASET,
      )({
        api,
        node: api.getRowNode(ROW),
        data: api.getRowNode(ROW).data,
        column: col,
        newValue: value,
        oldValue: "before",
        type: "cellValueChanged",
        source: "edit",
      });
      expect(body().new_value).toBe(value);
      pending[0].resolve({ data: {} });
      await observeNextEditor();
      expect(post).toHaveBeenCalledTimes(1);
      expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe(value);
    },
  );
});

// versionedCellEdit (the dataset page's grid handler, DevelopDataV2.jsx)
// refuses a number cell edit that is not a number and puts the old value
// back. That revert is display only: the user's edit was refused, so nothing
// may be written, even while Tab has opened the next editor.
describe("refused number edits with real AG Grid", () => {
  it("a refused float edit committed with Tab writes nothing while the next editor stays open", async () => {
    await mountGrid("float", "2.5");
    await refusedEdit(
      "not a number",
      "The cell keeps its value.",
      "Tab",
      ".ag-popup-editor textarea",
    );
    // Real elapsed time with the editor Tab opened: an echo would write here.
    await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
    expect(post).not.toHaveBeenCalled();
    // Only that editor is open; the refused cell is not being edited again.
    expect(api.getEditingCells().map((c) => c.column.getColId())).toEqual([
      NEXT,
    ]);
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("2.5");
  });

  it("a refused float edit committed with Enter is the no-write control", async () => {
    await mountGrid("float", "2.5");
    await refusedEdit(
      "not a number",
      "The cell keeps its value.",
      "Enter",
      ".ag-popup-editor textarea",
    );
    await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
    expect(post).not.toHaveBeenCalled();
    expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe("2.5");
  });
});

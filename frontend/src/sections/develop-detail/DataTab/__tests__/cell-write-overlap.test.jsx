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

// Two saves of the same cell overlap: "before" is edited to "first value",
// then to "second value", while both update_cell_value requests are pending.
// Whatever order the responses arrive in, the cell must end on the newest
// value that was saved, or on "before" when neither was. Every commit is
// sent (two POSTs), no response writes again, and no setDataValue is used.
//
// This uses the installed AG Grid 35.0.0 (EditService, popup editors, Tab
// navigation) with the application's real getter, valueSetter, grid handler
// and writer. Only HTTP is stubbed, and jsdom gets layout numbers.
const DATASET = "cell-overlap-dataset";
const ROW = "cell-overlap-row";
const COLUMN = "edited";
const NEXT = "next";
const OBSERVATION_MS = 2100;
let api;
let host;
let post;
let pending;

function column(id) {
  const config = getColumnConfig({
    eachCol: {
      id,
      name: id,
      data_type: "text",
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

async function mountGrid() {
  host = document.createElement("div");
  host.style.cssText = "width:800px;height:400px";
  document.body.append(host);
  api = createGrid(host, {
    columnDefs: [column(COLUMN), column(NEXT)],
    rowData: [
      {
        row_id: ROW,
        [COLUMN]: { cell_value: "before" },
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
  await waitFor(() => expect(editedCell()).not.toBeNull());
}

const editedCell = () =>
  host.querySelector(`[row-id="${ROW}"] [col-id="${COLUMN}"]`);
const editing = () => api.getEditingCells().map((c) => c.column.getColId());

// Tab opens the next cell's editor; Enter leaves no editor open.
async function edit(value, key) {
  const writesBefore = post.mock.calls.length;
  if (key === "Tab" && editing().length) api.stopEditing(true);
  // A real double click's mouse-down focuses the cell; jsdom's touch support
  // makes AG Grid skip mouse-down, so focus it as that mouse-down would.
  api.setFocusedCell(0, COLUMN);
  fireEvent.doubleClick(editedCell(), { detail: 2 });
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
  await waitFor(() => expect(post).toHaveBeenCalledTimes(writesBefore + 1));
  await waitFor(() => expect(editing()).toEqual(key === "Tab" ? [NEXT] : []));
}

// Both saves sent and pending; the cell shows the newest typed value.
async function twoPendingSaves(key) {
  await mountGrid();
  await edit("first value", key);
  await edit("second value", key);
  expect(post).toHaveBeenCalledTimes(2);
  for (const [index, value] of ["first value", "second value"].entries()) {
    expect(post.mock.calls[index][0]).toBe(
      endpoints.develop.updateCellValue(DATASET),
    );
    expect(post.mock.calls[index][1]).toEqual({
      column_id: COLUMN,
      row_id: ROW,
      new_value: value,
    });
  }
  await expectShown("second value");
}

// Let a response's handlers run (they are promise callbacks).
const afterResponse = () => new Promise((resolve) => setTimeout(resolve, 50));

async function expectShown(value) {
  expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe(value);
  await waitFor(() => expect(editedCell().textContent).toBe(value));
}

// Real elapsed time after the last response: an echo would be sent here, and
// would change which editor is open.
async function holdAfterLastResponse(key) {
  const open = key === "Tab" ? [NEXT] : [];
  expect(editing()).toEqual(open);
  await new Promise((resolve) => setTimeout(resolve, OBSERVATION_MS));
  expect(editing()).toEqual(open);
  expect(post).toHaveBeenCalledTimes(2);
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

afterEach(() => {
  api?.destroy();
  api = undefined;
  host?.remove();
  cleanup();
  setRealBackendForTests(null);
  vi.restoreAllMocks();
});

describe.each(["Tab", "Enter"])(
  "overlapping saves of one cell committed with %s, real AG Grid",
  (key) => {
    it("both fail, the second first: the cell returns to the value before both", async () => {
      await twoPendingSaves(key);
      pending[1].reject(new Error("second save failed"));
      await afterResponse();
      // The first save is still pending, so the cell shows its value.
      await expectShown("first value");
      pending[0].reject(new Error("first save failed"));
      await afterResponse();
      await holdAfterLastResponse(key);
      await expectShown("before");
    });

    it("both fail, the first first: the cell returns to the value before both", async () => {
      await twoPendingSaves(key);
      pending[0].reject(new Error("first save failed"));
      await afterResponse();
      // The newer save is still pending; an older failure does not show.
      await expectShown("second value");
      pending[1].reject(new Error("second save failed"));
      await afterResponse();
      await holdAfterLastResponse(key);
      await expectShown("before");
    });

    it("the first is saved, then the second fails: the cell shows the first, saved value", async () => {
      await twoPendingSaves(key);
      pending[0].resolve({ data: {} });
      await afterResponse();
      await expectShown("second value");
      pending[1].reject(new Error("second save failed"));
      await afterResponse();
      await holdAfterLastResponse(key);
      await expectShown("first value");
    });

    it("the second fails, then the first is saved: the cell shows the first, saved value", async () => {
      await twoPendingSaves(key);
      pending[1].reject(new Error("second save failed"));
      await afterResponse();
      await expectShown("first value");
      pending[0].resolve({ data: {} });
      await afterResponse();
      await holdAfterLastResponse(key);
      await expectShown("first value");
    });
  },
);

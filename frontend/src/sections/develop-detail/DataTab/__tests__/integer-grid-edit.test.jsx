import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

const DATASET = "integer-dataset";
const ROW = "integer-row";
const COLUMN = "integer-column";
const LARGE = "9007199254740993";
let api;
let host;
let post;
let get;
let stored;

const row = () => ({
  row_id: ROW,
  [COLUMN]: {
    cell_value: stored,
    status: "error",
    value_infos: '{"error":"Existing diagnostic"}',
  },
});
const cell = () => host.querySelector(`[row-id="${ROW}"] [col-id="${COLUMN}"]`);

async function mountGrid(initial = "17", versioned = true) {
  stored = initial;
  const queryClient = { invalidateQueries: vi.fn() };
  const config = getColumnConfig({
    eachCol: {
      id: COLUMN,
      name: "Exact integer",
      data_type: "integer",
      origin_type: "dataset",
      is_visible: true,
    },
    queryClient,
    dataset: DATASET,
    versioned,
  });
  // Keep the real getter, setter, data type, editor and writer. Only the
  // presentation-only React renderer/header/menu are omitted from this grid.
  const {
    field,
    headerName,
    dataType,
    editable,
    cellDataType,
    valueGetter,
    valueSetter,
    cellEditor,
    cellEditorParams,
    cellEditorPopup,
  } = config;
  const immediate = onCellValueChangedWrapper(queryClient, DATASET);
  host = document.createElement("div");
  host.style.cssText = "width:800px;height:400px";
  document.body.append(host);
  api = createGrid(host, {
    columnDefs: [
      {
        field,
        headerName,
        dataType,
        editable,
        cellDataType,
        valueGetter,
        valueSetter,
        cellEditor,
        cellEditorParams,
        cellEditorPopup,
      },
    ],
    rowData: [row()],
    getRowId: ({ data }) => data.row_id,
    onCellValueChanged: versioned
      ? versionedCellEdit({
          datasetId: DATASET,
          fallback: immediate,
          mediaPending: true,
        })
      : immediate,
    stopEditingWhenCellsLoseFocus: true,
    suppressColumnVirtualisation: true,
    suppressRowVirtualisation: true,
    animateRows: false,
  });
  await waitFor(() => expect(cell()).not.toBeNull());
}

async function openEditor() {
  // Match mouse focus explicitly: jsdom advertises touch support to AG Grid.
  api.setFocusedCell(0, COLUMN);
  fireEvent.doubleClick(cell(), { detail: 2 });
  await waitFor(() =>
    expect(document.querySelector(".ag-popup-editor input")).not.toBeNull(),
  );
  return document.querySelector(".ag-popup-editor input");
}

async function commit(input, value) {
  input.focus();
  fireEvent.input(input, { target: { value } });
  fireEvent.change(input, { target: { value } });
  expect(input.value).toBe(value);
  fireEvent.keyDown(input, {
    key: "Enter",
    code: "Enter",
    keyCode: 13,
    which: 13,
  });
  await waitFor(() => expect(api.getEditingCells()).toEqual([]));
}

beforeEach(() => {
  render(<SnackbarProvider />);
  setRealBackendForTests(true);
  // HTTP is the only behavior stubbed. The backend companion tests exercise
  // the real JSON parser, integer validation, persistence and table endpoint.
  post = vi.spyOn(axios, "post").mockImplementation(async (_url, body) => {
    stored = body.new_value;
    return { data: { result: "Saved" } };
  });
  get = vi.spyOn(axios, "get").mockImplementation(async () => ({
    data: JSON.parse(JSON.stringify({ result: { table: [row()] } })),
  }));
  vi.spyOn(HTMLElement.prototype, "offsetParent", "get").mockImplementation(
    function () {
      return this.parentElement;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
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

describe("exact integer editing with real AG Grid", () => {
  it.each([true, false])(
    "preserves large integer edits, reads and reopened editors (versioned=%s)",
    async (versioned) => {
      await mountGrid("17", versioned);
      const input = await openEditor();
      expect(input.value).toBe("17");
      await commit(input, LARGE);
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      expect(post).toHaveBeenCalledWith(
        endpoints.develop.updateCellValue(DATASET),
        { column_id: COLUMN, row_id: ROW, new_value: LARGE },
      );
      expect(stored).toBe(LARGE);
      await waitFor(() => expect(cell().textContent).toBe(LARGE));
      expect(api.getRowNode(ROW).data[COLUMN].cell_value).toBe(LARGE);

      const url = endpoints.develop.getDatasetDetail(DATASET);
      const response = await axios.get(url);
      expect(get).toHaveBeenCalledWith(url);
      expect(response.data.result.table[0][COLUMN].cell_value).toBe(LARGE);
      api.setGridOption("rowData", response.data.result.table);
      await waitFor(() => expect(cell().textContent).toBe(LARGE));
      const reopened = await openEditor();
      expect(reopened.value).toBe(LARGE);
      // Reapply the same integer with a decimal suffix, forcing a real edit
      // while checking normalization never changes any of its integer digits.
      await commit(reopened, `${LARGE}.0`);
      await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
      expect(post.mock.calls[1]).toEqual([
        endpoints.develop.updateCellValue(DATASET),
        { column_id: COLUMN, row_id: ROW, new_value: LARGE },
      ]);
      expect(stored).toBe(LARGE);
      await waitFor(() => expect(cell().textContent).toBe(LARGE));
    },
  );

  it("opens a stored large integer without rounding", async () => {
    await mountGrid(LARGE);
    expect(cell().textContent).toBe(LARGE);
    const input = await openEditor();
    expect(input.value).toBe(LARGE);
    await commit(input, "9007199254740995");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({
      column_id: COLUMN,
      row_id: ROW,
      new_value: "9007199254740995",
    });
    expect(stored).toBe("9007199254740995");
  });

  it.each(["1.0000000000000001", "1e-400", "9007199254740993.1"])(
    "refuses fractional text %s in the immediate grid writer",
    async (draft) => {
      await mountGrid("17", false);
      await commit(await openEditor(), draft);
      await waitFor(() =>
        expect(document.body.textContent).toContain("whole number"),
      );
      expect(post).not.toHaveBeenCalled();
      expect(stored).toBe("17");
      expect(cell().textContent).toBe("17");
      expect(api.getRowNode(ROW).data[COLUMN]).toEqual(row()[COLUMN]);
    },
  );

  it("refuses an unsafe Number passed to the writer", async () => {
    await mountGrid();
    const node = api.getRowNode(ROW);
    onCellValueChangedWrapper(
      { invalidateQueries: vi.fn() },
      DATASET,
    )({
      api,
      node,
      data: node.data,
      column: api.getColumn(COLUMN),
      newValue: 9007199254740992,
      oldValue: "17",
      type: "cellValueChanged",
      source: "edit",
    });
    await waitFor(() =>
      expect(document.body.textContent).toContain("as decimal text"),
    );
    expect(post).not.toHaveBeenCalled();
    expect(stored).toBe("17");
    expect(node.data[COLUMN]).toEqual(row()[COLUMN]);
  });
});

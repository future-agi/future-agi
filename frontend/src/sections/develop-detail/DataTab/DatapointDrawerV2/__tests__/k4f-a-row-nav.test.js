// integration (DE-02): after J / K opened another row, ↑ / ↓ in the grid continue
// from the open row: the grid's focused cell follows it while the keyboard
// is in the grid (and is left alone when it is not).
import { createElement } from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as rowNav from "../useRowNavigation";

const { followFocus } = rowNav;
const useRowNavigation = rowNav.default;

// PF-2 (PUX-9): the hook's server read and the grid are stubs; the
// navigation logic between them is real.
const h = vi.hoisted(() => ({
  view: null,
  api: null,
  post: null,
  get: null,
  failedOnly: false,
  failedEvalIds: [],
  read: null,
}));
vi.mock("src/api/develop/row-view", async (importOriginal) => ({
  ...(await importOriginal()),
  useRowInView: (...args) => {
    h.read = args;
    return {
      data: h.view,
      isLoading: false,
      isError: false,
      refetch: () => {},
    };
  },
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: {
      ...real.default,
      post: (...a) => h.post(...a),
      get: (...a) => h.get(...a),
    },
  };
});
vi.mock("src/api/develop/dataset-versions", () => ({
  useAsOfVersion: () => ({ isAsOf: false, isLoading: false, version: null }),
}));
vi.mock("../../Versions/versionedSource", () => ({ useAsOfView: () => ({}) }));
vi.mock("src/sections/develop-detail/states", () => ({
  useDevelopFilterStore: (select) => select({ filters: [] }),
  useDevelopSearchStore: (select) => select({ search: "" }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: h.api } }),
}));
vi.mock("../../DataGrid/useDataGridView", () => ({
  useDataGridView: () => ({
    failedOnly: h.failedOnly,
    failedEvalIds: h.failedEvalIds,
    splitIds: [],
    cellErrorOnly: false,
  }),
}));

const mount = () => {
  const grid = document.createElement("div");
  grid.className = "develop-data-grid";
  const cell = document.createElement("button");
  grid.appendChild(cell);
  document.body.appendChild(grid);
  const outside = document.createElement("button");
  document.body.appendChild(outside);
  return { grid, cell, outside };
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("followFocus", () => {
  it("moves a stale focused cell to the open row (same column)", () => {
    const { cell } = mount();
    cell.focus();
    const api = {
      getFocusedCell: () => ({ rowIndex: 2, column: "col-a", rowPinned: null }),
      setFocusedCell: vi.fn(),
    };
    followFocus(api, { rowIndex: 4 });
    expect(api.setFocusedCell).toHaveBeenCalledWith(4, "col-a", null);
  });

  it("does nothing when the focused cell is already on the open row", () => {
    const { cell } = mount();
    cell.focus();
    const api = {
      getFocusedCell: () => ({ rowIndex: 4, column: "col-a" }),
      setFocusedCell: vi.fn(),
    };
    followFocus(api, { rowIndex: 4 });
    expect(api.setFocusedCell).not.toHaveBeenCalled();
  });

  it("leaves focus alone when the keyboard is outside the grid", () => {
    const { outside } = mount();
    outside.focus();
    const api = {
      getFocusedCell: () => ({ rowIndex: 2, column: "col-a" }),
      setFocusedCell: vi.fn(),
    };
    followFocus(api, { rowIndex: 4 });
    expect(api.setFocusedCell).not.toHaveBeenCalled();
  });

  it("does nothing without a focused cell", () => {
    const { cell } = mount();
    cell.focus();
    const api = { getFocusedCell: () => null, setFocusedCell: vi.fn() };
    followFocus(api, { rowIndex: 4 });
    expect(api.setFocusedCell).not.toHaveBeenCalled();
  });
});

// Persisted rows and dataset IDs are UUIDs, including the split membership read.
const DATASET_ID = "11111111-1111-4111-8111-111111111111";
const ROW_1 = "22222222-2222-4222-8222-222222222221";
const ROW_2 = "22222222-2222-4222-8222-222222222222";
const ROW_3 = "22222222-2222-4222-8222-222222222223";
const ROW_9 = "22222222-2222-4222-8222-222222222229";
// A grid with three rows loaded (one block).
const gridApi = () => {
  const nodes = [ROW_1, ROW_2, ROW_3].map((id, rowIndex) => ({
    id,
    rowIndex,
    data: { row_id: id, order: rowIndex },
  }));
  return {
    getRowNode: (id) => nodes.find((n) => n.id === id),
    getDisplayedRowAtIndex: (i) => nodes[i],
    getColumnState: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    ensureNodeVisible: () => {},
    ensureIndexVisible: () => {},
    getVerticalPixelRange: () => null,
    getFocusedCell: () => null,
  };
};
const loadedView = (extra = {}) => ({
  current: { row_id: ROW_2, order: 1 },
  prev: null,
  next: [ROW_3],
  position: { index: null, total: null },
  outside: false,
  notFound: false,
  ...extra,
});
const wrapper = ({ children }) => createElement(MemoryRouter, null, children);
const mountNav = (openRow) =>
  renderHook(
    () => useRowNavigation({ datasetId: DATASET_ID, rowId: ROW_2, openRow }),
    { wrapper },
  );

describe("previous row on a backend that sends no prev (PF-2)", () => {
  afterEach(() => {
    h.view = null;
    h.api = null;
    h.post = null;
    h.get = null;
    h.failedOnly = false;
    h.failedEvalIds = [];
    h.read = null;
  });

  it("keeps selected evaluator IDs in the real row-reader query", () => {
    h.api = gridApi();
    h.view = loadedView();
    h.failedOnly = true;
    h.failedEvalIds = ["quality"];
    mountNav(vi.fn());
    expect(h.read[2].filters).toContainEqual({
      column_id: "system:row_failed_in",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: ["quality"],
      },
    });
  });

  it("get-row-data without prev reads as not known (null), not as no previous row", async () => {
    const { fetchRowInView } = await vi.importActual(
      "src/api/develop/row-view",
    );
    h.post = vi.fn(async () => ({
      data: {
        status: true,
        result: {
          next: { row_id: [ROW_3] },
          current: { row_id: ROW_2, order: 1 },
        },
      },
    }));
    // Splits are visible at launch: stub the membership transport as well as
    // get-row-data. No real HTTP request may escape this navigation fixture.
    h.get = vi.fn(async () => ({ data: { result: { split_ids: [] } } }));
    const v = await fetchRowInView(DATASET_ID, ROW_2, {});
    expect(h.get).toHaveBeenNthCalledWith(
      1,
      `/model-hub/datasets/${DATASET_ID}/splits/membership/`,
      { params: { row_id: ROW_2 } },
    );
    expect(v.prev).toBeNull();
    expect(v.next).toEqual([ROW_3]);
    // A server that does send prev keeps it.
    h.post = vi.fn(async () => ({
      data: {
        status: true,
        result: {
          prev: { row_id: [] },
          next: { row_id: [ROW_2] },
          current: { row_id: ROW_1, order: 0 },
        },
      },
    }));
    expect((await fetchRowInView(DATASET_ID, ROW_1, {})).prev).toEqual([]);
    expect(h.get).toHaveBeenNthCalledWith(
      2,
      `/model-hub/datasets/${DATASET_ID}/splits/membership/`,
      { params: { row_id: ROW_1 } },
    );
    expect(h.get).toHaveBeenCalledTimes(2);
  });

  it("the loaded Row view on row 2 goes up to row 1 through the grid", () => {
    h.api = gridApi();
    h.view = loadedView();
    const openRow = vi.fn();
    const { result } = mountNav(openRow);
    expect(result.current.loaded).toBe(true);
    expect(result.current.prevDisabled).toBe(false);
    act(() => result.current.goPrev());
    expect(openRow).toHaveBeenCalledWith(ROW_1, { push: false });
    // next still comes from the server's ids.
    expect(result.current.nextDisabled).toBe(false);
  });

  it("a server that sends prev decides (first row: up is off)", () => {
    h.api = gridApi();
    h.view = loadedView({ prev: [] });
    const openRow = vi.fn();
    const { result } = mountNav(openRow);
    expect(result.current.prevDisabled).toBe(true);
    act(() => result.current.goPrev());
    expect(openRow).not.toHaveBeenCalled();
  });

  it("outside the filtered set both chevrons stay off", () => {
    h.api = gridApi();
    h.view = loadedView({ prev: [], next: [], outside: true });
    const { result } = mountNav(vi.fn());
    expect(result.current.prevDisabled).toBe(true);
    expect(result.current.nextDisabled).toBe(true);
  });

  it("rowNavTargets (pure): null prev falls back to the grid neighbour", () => {
    expect(typeof rowNav.rowNavTargets).toBe("function");
    const gridNeighbour = (step) => (step < 0 ? ROW_1 : ROW_3);
    expect(
      rowNav.rowNavTargets({
        loaded: true,
        outside: false,
        view: { prev: null, next: [ROW_9] },
        gridNeighbour,
      }),
    ).toEqual({
      prev: ROW_1,
      next: ROW_9,
      prevDisabled: false,
      nextDisabled: false,
    });
    expect(
      rowNav.rowNavTargets({
        loaded: false,
        outside: false,
        view: null,
        gridNeighbour,
      }),
    ).toMatchObject({ prev: ROW_1, next: ROW_3 });
  });
});

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useDataToolbarView from "../useDataToolbarView";

const mocks = vi.hoisted(() => {
  const grid = {
    readyAt: 1,
    setSort: vi.fn(),
    setFailedOnly: vi.fn(),
    setFailedEvals: vi.fn(),
    setDensity: vi.fn(),
    setExpression: vi.fn(),
    setStatuses: vi.fn(),
    setTags: vi.fn(),
  };
  const api = {
    getColumns: vi.fn(),
    applyColumnState: vi.fn(),
    onFilterChanged: vi.fn(),
  };
  return { grid, api, patch: vi.fn(), search: vi.fn(), params: vi.fn() };
});

vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
vi.mock("lodash", () => ({ default: { debounce: (fn) => fn } }));
vi.mock("react-router-dom", () => ({ useSearchParams: () => mocks.params() }));
vi.mock("../useDataGridView", () => ({ useDataGridView: () => mocks.grid }));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, mocks.patch],
}));
vi.mock("../../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: mocks.api } }),
}));
vi.mock("../../../states", () => ({
  useDevelopSearchStore: () => ({ search: "", setSearch: mocks.search }),
  useDevelopFilterStoreShallow: () => ({ filters: [] }),
  useDevelopFilterStore: {
    getState: () => ({ resetFilters: vi.fn(), setFilters: vi.fn() }),
  },
}));

const sort = [{ colId: "saved-column", sort: "desc" }];
beforeEach(() => {
  vi.clearAllMocks();
  mocks.grid.readyAt = 1;
  mocks.api.getColumns.mockReturnValue([]);
  mocks.api.applyColumnState.mockReturnValue(false);
  mocks.params.mockReturnValue([
    new URLSearchParams({
      grid_view: JSON.stringify({ q: "", density: "tall", filters: [], sort }),
    }),
    vi.fn(),
  ]);
});

describe("saved grid link hydration", () => {
  it("waits for async columns before marking the link as applied", () => {
    const { rerender } = renderHook(() => useDataToolbarView("dataset"));
    expect(mocks.grid.setSort).not.toHaveBeenCalled();
    expect(mocks.patch).not.toHaveBeenCalled();

    mocks.grid.readyAt = 2;
    mocks.api.getColumns.mockReturnValue([{ getColId: () => "saved-column" }]);
    mocks.api.applyColumnState.mockReturnValue(true);
    rerender();
    expect(mocks.api.applyColumnState).toHaveBeenCalledWith({
      state: [{ colId: "saved-column", sort: "desc", sortIndex: 0 }],
      defaultState: { sort: null, sortIndex: null },
    });
    expect(mocks.grid.setSort).toHaveBeenCalledWith(sort);
    expect(mocks.patch).toHaveBeenCalledTimes(1);
    rerender();
    expect(mocks.patch).toHaveBeenCalledTimes(1);
  });
});

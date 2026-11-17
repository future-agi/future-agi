// F49 link hydration: a linked view's splits apply only with dataset.splits,
// as its Failed only follows dataset.filtersSystem.pills.
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import useDataToolbarView from "../useDataToolbarView";

const mocks = vi.hoisted(() => {
  const grid = {
    readyAt: 1,
    pendingSplitNames: null,
    setSort: vi.fn(),
    setFailedOnly: vi.fn(),
    setFailedEvals: vi.fn(),
    setDensity: vi.fn(),
    setExpression: vi.fn(),
    setStatuses: vi.fn(),
    setTags: vi.fn(),
    setPendingSplitNames: vi.fn(),
  };
  const api = {
    getColumns: () => [{ getColId: () => "column" }],
    applyColumnState: vi.fn(),
    onFilterChanged: vi.fn(),
  };
  return { grid, api, shown: new Set(), params: vi.fn(), setParams: vi.fn() };
});

vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: (id) => mocks.shown.has(id),
}));
vi.mock("lodash", () => ({ default: { debounce: (fn) => fn } }));
vi.mock("react-router-dom", () => ({ useSearchParams: () => mocks.params() }));
vi.mock("../useDataGridView", () => ({ useDataGridView: () => mocks.grid }));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, vi.fn()],
}));
vi.mock("../../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: mocks.api } }),
}));
vi.mock("../../../states", () => ({
  useDevelopSearchStore: () => ({ search: "", setSearch: vi.fn() }),
  useDevelopFilterStoreShallow: () => ({ filters: [] }),
  useDevelopFilterStore: {
    getState: () => ({ resetFilters: vi.fn(), setFilters: vi.fn() }),
  },
}));

const view = { q: "", density: "compact", filters: [], sort: [] };
// Opens a grid view link and returns the URL params the toolbar wrote.
const openLink = (state) => {
  mocks.params.mockReturnValue([
    new URLSearchParams({ grid_view: JSON.stringify({ ...view, ...state }) }),
    mocks.setParams,
  ]);
  renderHook(() => useDataToolbarView("dataset"));
  return mocks.setParams.mock.calls.at(-1)?.[0];
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.grid.pendingSplitNames = null;
  mocks.shown = new Set(["dataset.savedViews", "dataset.splits"]);
});

describe("saved grid link split hydration", () => {
  it("applies a linked view's splits through the split filter", () => {
    const params = openLink({ splits: "train" });
    expect(mocks.grid.setPendingSplitNames).toHaveBeenCalledWith("train");
    expect(params.get("splits")).toBe("train");
  });

  it("ignores a linked view's splits while dataset.splits is hidden", () => {
    mocks.shown.delete("dataset.splits");
    const params = openLink({ splits: "train" });
    expect(mocks.grid.setDensity).toHaveBeenCalledWith("compact");
    expect(mocks.grid.setPendingSplitNames).not.toHaveBeenCalled();
    expect(params.has("splits")).toBe(false);
  });

  it("drops an unresolved split request when a view without splits applies", () => {
    mocks.grid.pendingSplitNames = "train";
    openLink({});
    expect(mocks.grid.setPendingSplitNames).toHaveBeenCalledWith(null);
  });
});

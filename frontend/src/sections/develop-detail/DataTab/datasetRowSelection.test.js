import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDevelopFilterStore, useDevelopSearchStore, useDevelopSelectedRowsStore } from "../states";
import {
  captureRowSelection, getDatasetSelectionScope, isDatasetSelectionScopeCurrent,
  recordGridSelection, resolveSelectedRowIds,
} from "./datasetRowSelection";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get }, endpoints: { develop: { getDatasetDetail: (id) => `/datasets/${id}` } },
}));

let api;
const select = (total = 2, excluded = []) => {
  api = {
    getGridOption: () => ({ selectionScope: getDatasetSelectionScope("dataset-a"), totalRowCount: total }),
    getServerSideSelectionState: () => ({ selectAll: true, toggledNodes: excluded }),
    deselectAll: vi.fn(),
  };
  recordGridSelection(api);
  return captureRowSelection("dataset-a", api);
};
const response = (ids, total, index = 0) => ({ data: { result: {
  table: ids.map((row_id) => ({ row_id })),
  metadata: {
    total_rows: total, page_size: 100, current_page_index: index,
    total_pages: Math.ceil(total / 100), has_more: (index + 1) * 100 < total,
    next_page_index: (index + 1) * 100 < total ? index + 1 : null, error_messages: [],
  },
} } });
const resolve = (selection) => resolveSelectedRowIds(selection, () => {
  // The component revalidates this same snapshot immediately before dispatch.
  const live = captureRowSelection("dataset-a", api);
  if (live.revision !== selection.revision) throw new Error("Selection changed");
});

beforeEach(() => {
  get.mockReset();
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.getState().setSearch("alpha");
  useDevelopSelectedRowsStore.getState().resetSelectedRows();
});

describe("dataset selection scope and page resolution", () => {
  it("atomically resets the actual selection keys", () => {
    select(2, ["alpha-1"]);
    useDevelopSelectedRowsStore.getState().resetSelectedRows();
    expect(useDevelopSelectedRowsStore.getState()).toMatchObject({ selectAll: false, toggledNodes: [], selectionScope: null });
  });

  it("preserves selection for unchanged search and ineffective filter drafts", () => {
    const selection = select();
    useDevelopSearchStore.getState().setSearch("alpha");
    useDevelopFilterStore.getState().setFilters([{ columnId: "", filterConfig: {
      filterType: "text", filterOp: "equals", filterValue: "draft",
    } }]);
    expect(captureRowSelection("dataset-a", api).revision).toBe(selection.revision);
  });

  it("does not revive an alpha selection after alpha to beta to alpha", () => {
    const scope = getDatasetSelectionScope("dataset-a");
    select();
    useDevelopSearchStore.getState().setSearch("beta");
    useDevelopSearchStore.getState().setSearch("alpha");
    expect(isDatasetSelectionScopeCurrent(scope, "dataset-a")).toBe(false);
    api.getGridOption = () => ({ selectionScope: scope, totalRowCount: 2 });
    recordGridSelection(api);
    expect(useDevelopSelectedRowsStore.getState().selectAll).toBe(false);
    expect(api.deselectAll).toHaveBeenCalled();
  });

  it("rejects selection on old rows during the search debounce", () => {
    const oldScope = getDatasetSelectionScope("dataset-a");
    select();
    useDevelopSearchStore.getState().setSearch("beta");
    api.getGridOption = () => ({ selectionScope: oldScope, totalRowCount: 2 });
    recordGridSelection(api);
    expect(() => captureRowSelection("dataset-a", api)).toThrow();
  });

  it("rejects a snapshot belonging to another dataset", () => {
    select();
    expect(() => captureRowSelection("dataset-b", api)).toThrow();
  });

  it("enumerates every page under the captured effective filters and subtracts exclusions", async () => {
    useDevelopFilterStore.getState().setFilters([{ columnId: "tag", filterConfig: {
      filterType: "text", filterOp: "equals", filterValue: "approved",
    } }]);
    const ids = Array.from({ length: 102 }, (_, i) => `alpha-${i}`);
    const selection = select(102, ["alpha-1", "alpha-101"]);
    get.mockResolvedValueOnce(response(ids.slice(0, 100), 102));
    get.mockResolvedValueOnce(response(ids.slice(100), 102, 1));
    expect(await resolve(selection)).toEqual(ids.filter((id) => !["alpha-1", "alpha-101"].includes(id)));
    expect(get).toHaveBeenCalledTimes(2);
    for (const [, { params }] of get.mock.calls) {
      expect(JSON.parse(params.filters)).toEqual(selection.scope.filters);
      expect(JSON.parse(params.search).key).toBe("alpha");
      expect(params.exact_snapshot).toBeUndefined();
    }
    expect(get.mock.calls[1][1].params.current_page_index).toBe(1);
  });

  it.each(["duplicate IDs", "changed count", "page error", "missing ID", "wrong page", "wrong continuation"])("rejects %s without returning partial IDs", async (kind) => {
    const selection = select();
    const data = response(["alpha-1", "alpha-2"], 2);
    const result = data.data.result;
    if (kind === "duplicate IDs") result.table[1].row_id = "alpha-1";
    if (kind === "changed count") result.metadata.total_rows = 3;
    if (kind === "page error") result.metadata.error_messages = ["Filter failed"];
    if (kind === "missing ID") result.table[1] = {};
    if (kind === "wrong page") result.metadata.current_page_index = 1;
    if (kind === "wrong continuation") result.metadata.has_more = true;
    get.mockResolvedValueOnce(data);
    await expect(resolve(selection)).rejects.toThrow();
  });

  it("rejects a missing excluded ID instead of retargeting the selection", async () => {
    const selection = select(2, ["old-alpha"]);
    get.mockResolvedValueOnce(response(["alpha-1", "alpha-2"], 2));
    await expect(resolve(selection)).rejects.toThrow();
  });

  it("rejects stale reads between pages", async () => {
    const ids = Array.from({ length: 101 }, (_, i) => `alpha-${i}`);
    const selection = select(101);
    get.mockImplementationOnce(async () => {
      useDevelopSearchStore.getState().setSearch("");
      return response(ids.slice(0, 100), 101);
    });
    await expect(resolve(selection)).rejects.toThrow();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("rejects a new selection made while a read is pending", async () => {
    const selection = select();
    get.mockImplementationOnce(async () => {
      api.getServerSideSelectionState = () => ({ selectAll: false, toggledNodes: ["alpha-1"] });
      recordGridSelection(api);
      return response(["alpha-1", "alpha-2"], 2);
    });
    await expect(resolve(selection)).rejects.toThrow();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  refreshGrid: vi.fn(),
  notify: vi.fn(),
  gridApi: { current: null },
  dataset: "dataset-a",
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post, delete: mocks.delete },
  endpoints: { develop: {
    getDatasetDetail: (id) => `/datasets/${id}/table/`,
    deleteDatasetRow: (id) => `/datasets/${id}/delete/`,
    duplicateDatasetRows: (id) => `/datasets/${id}/duplicate/`,
  } },
}));
vi.mock("src/api/develop/develop-detail", async (original) => ({
  ...(await original()),
  useGetColumns: () => [],
  useDevelopDatasetList: () => ({ data: [] }),
}));
vi.mock("react-router", async (original) => ({
  ...(await original()),
  useParams: () => ({ dataset: mocks.dataset }),
}));
vi.mock("../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: mocks.gridApi, refreshGrid: mocks.refreshGrid }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Owner" }) }));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { UPDATE: "update", DELETE: "delete" },
  RolePermission: { DATASETS: { update: { Owner: true }, delete: { Owner: true } } },
}));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, PropertyName: {}, trackEvent: vi.fn() }));
vi.mock("src/utils/Mixpanel/EventNames", () => ({ Events: {} }));
vi.mock("src/utils/Mixpanel/mixpanel", () => ({ trackEvent: vi.fn() }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: mocks.notify }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/svg-color/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/ColumnDropdown/ColumnDropdown", () => ({ default: () => null }));
vi.mock("src/components/ColumnResizer/ColumnResizer", () => ({ default: () => null }));
vi.mock("../DataTab/DataMenuList/DataMenuList", () => ({ default: () => null }));
vi.mock("../DataTab/RunEvals/RunEvals", () => ({ default: () => null }));
vi.mock("../Common/SnackbarWithAction", () => ({ default: () => null }));

/* eslint-disable react/prop-types */
vi.mock("src/components/iconify/iconify", () => ({
  default: ({ onClick }) => <button type="button" aria-label="Clear search" onClick={onClick} />,
}));
vi.mock("src/components/tooltip/CustomTooltip", () => ({ default: ({ children }) => children }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ searchQuery, onChange, InputProps }) => <>
    <input aria-label="Search rows" value={searchQuery} onChange={onChange} />
    {InputProps.endAdornment}
  </>,
}));
vi.mock("../Common/CustomDialog/CustomDialog", () => ({
  default: ({ open, actionButton, onClickAction, onClose, loading, children }) => open && (
    <div role="dialog">
      {children}
      <button type="button" disabled={loading} onClick={onClickAction}>Confirm {actionButton}</button>
      <button type="button" onClick={onClose}>Close dialog</button>
    </div>
  ),
}));
vi.mock("../DataTab/Delete/DeleteRowAction", () => ({
  default: ({ selectedCount }) => <span>Delete {selectedCount} rows</span>,
}));
vi.mock("../DataTab/Duplicate/DuplicateRowAction", () => ({
  default: ({ setNoOfCopies }) => <input aria-label="Copies" onChange={(event) => setNoOfCopies(event.target.value)} />,
}));
/* eslint-enable react/prop-types */

import DevelopDataSelectionActive from "./DevelopDataSelectionActive";
import DevelopBarLeftSection from "./DevelopBarLeftSection";
import { getDatasetQueryKey } from "src/api/develop/develop-detail";
import { getDatasetSelectionScope, recordGridSelection } from "../DataTab/datasetRowSelection";
import { useDevelopFilterStore, useDevelopSearchStore, useDevelopSelectedRowsStore } from "../states";

const page = (ids, total = ids.length, index = 0) => ({ data: { result: {
  table: ids.map((row_id) => ({ row_id })),
  metadata: {
    total_rows: total, page_size: 100, current_page_index: index,
    total_pages: Math.ceil(total / 100), has_more: (index + 1) * 100 < total,
    next_page_index: (index + 1) * 100 < total ? index + 1 : null,
    error_messages: [],
  },
} } });

const selectRows = (selectAll = true, toggledNodes = []) => {
  const scope = getDatasetSelectionScope(mocks.dataset);
  mocks.gridApi.current.context = { selectionScope: scope, totalRowCount: 2 };
  mocks.gridApi.current.selection = { selectAll, toggledNodes };
  recordGridSelection(mocks.gridApi.current);
};

const renderActions = () => {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  client.setQueryData(getDatasetQueryKey(mocks.dataset, 0, [], [], ""), page(["alpha-1", "alpha-2", "beta-1", "beta-2"]));
  return render(
    <QueryClientProvider client={client}>
      <DevelopBarLeftSection hideFilter hideColumnView hideRowHeight />
      <DevelopDataSelectionActive />
    </QueryClientProvider>,
  );
};

const openAction = (action) => {
  fireEvent.click(screen.getByRole("button", { name: action, exact: true }));
  if (action === "Duplicate") fireEvent.change(screen.getByLabelText("Copies"), { target: { value: "1" } });
};
const confirm = (action) => fireEvent.click(screen.getByRole("button", { name: `Confirm ${action}` }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.dataset = "dataset-a";
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.getState().setSearch("alpha");
  useDevelopSelectedRowsStore.getState().resetSelectedRows();
  mocks.gridApi.current = {
    context: null,
    selection: null,
    getGridOption: (key) => mocks.gridApi.current[key],
    getServerSideSelectionState: () => mocks.gridApi.current.selection,
    deselectAll: vi.fn(),
    onFilterChanged: vi.fn(),
  };
  mocks.get.mockResolvedValue(page(["alpha-1", "alpha-2"]));
  mocks.delete.mockResolvedValue({ data: {} });
  mocks.post.mockResolvedValue({ data: {} });
  selectRows();
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("dataset row actions use the selected query scope", () => {
  it.each(["Delete", "Duplicate"])("%s sends only the initial alpha matches, never the hidden beta rows", async (action) => {
    renderActions();
    openAction(action);
    confirm(action);
    const mutation = action === "Delete" ? mocks.delete : mocks.post;
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(1));
    const payload = action === "Delete" ? mutation.mock.calls[0][1].data : mutation.mock.calls[0][1];
    expect(payload).toMatchObject({ row_ids: ["alpha-1", "alpha-2"], selected_all_rows: false });
    expect(mocks.get.mock.calls[0][1].params).toMatchObject({
      search: JSON.stringify({ key: "alpha", type: ["text", "image", "audio"] }),
      filters: "[]", current_page_index: 0,
    });
  });

  it.each(["Delete", "Duplicate"])("%s subtracts exclusions from the filtered matches", async (action) => {
    selectRows(true, ["alpha-1"]);
    renderActions(); openAction(action); confirm(action);
    const mutation = action === "Delete" ? mocks.delete : mocks.post;
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(1));
    const payload = action === "Delete" ? mutation.mock.calls[0][1].data : mutation.mock.calls[0][1];
    expect(payload.row_ids).toEqual(["alpha-2"]);
    expect(payload.selected_all_rows).toBe(false);
  });

  it.each(["beta", ""])("clears the grid and store before publishing search '%s', without waiting for debounce", (nextSearch) => {
    vi.useFakeTimers();
    renderActions(); openAction("Delete");
    let searchAtDeselect;
    mocks.gridApi.current.deselectAll.mockImplementation(() => { searchAtDeselect = useDevelopSearchStore.getState().search; });
    const observed = [];
    const unsubscribe = useDevelopSearchStore.subscribe(() => {
      const { selectAll, toggledNodes } = useDevelopSelectedRowsStore.getState();
      observed.push({ selectAll, toggledNodes });
    });
    if (nextSearch) fireEvent.change(screen.getByLabelText("Search rows"), { target: { value: nextSearch } });
    else fireEvent.click(screen.getByLabelText("Clear search"));
    unsubscribe();
    expect(searchAtDeselect).toBe("alpha");
    expect(observed).toEqual([{ selectAll: false, toggledNodes: [] }]);
    expect(mocks.gridApi.current.onFilterChanged).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.delete).not.toHaveBeenCalled();
  });

  it.each(["Delete", "Duplicate"])("%s rejects a delayed alpha read after changing to equally sized beta results", async (action) => {
    let finish;
    mocks.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    renderActions(); openAction(action); confirm(action);
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    fireEvent.change(screen.getByLabelText("Search rows"), { target: { value: "beta" } });
    await act(async () => { finish(page(["alpha-1", "alpha-2"])); });
    expect(mocks.delete).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
    // Fresh selection is required and then addresses beta only.
    act(() => selectRows());
    mocks.get.mockResolvedValue(page(["beta-1", "beta-2"]));
    openAction(action); confirm(action);
    const mutation = action === "Delete" ? mocks.delete : mocks.post;
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(1));
    const payload = action === "Delete" ? mutation.mock.calls[0][1].data : mutation.mock.calls[0][1];
    expect(payload.row_ids).toEqual(["beta-1", "beta-2"]);
  });

  it("rejects a stale confirmation after the filter changes", () => {
    renderActions(); openAction("Delete");
    act(() => useDevelopFilterStore.getState().setFilters([{ columnId: "tag", filterConfig: {
      filterType: "text", filterOp: "equals", filterValue: "approved",
    } }]));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.delete).not.toHaveBeenCalled();
  });

  it("rejects an in-flight read when the component unmounts", async () => {
    let finish;
    mocks.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const view = renderActions(); openAction("Delete"); confirm("Delete");
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    view.unmount();
    await act(async () => { finish(page(["alpha-1", "alpha-2"])); });
    expect(mocks.delete).not.toHaveBeenCalled();
  });

  it.each(["empty", "incomplete", "error"])("never dispatches a mutation after %s resolution", async (kind) => {
    if (kind === "error") mocks.get.mockRejectedValueOnce(new Error("Read failed"));
    else mocks.get.mockResolvedValueOnce(page(kind === "empty" ? [] : ["alpha-1"], 2));
    renderActions(); openAction("Delete"); confirm("Delete");
    await waitFor(() => expect(mocks.notify).toHaveBeenCalled());
    expect(mocks.delete).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("keeps an explicit selection explicit without enumerating the dataset", async () => {
    selectRows(false, ["alpha-2"]);
    renderActions(); openAction("Delete"); confirm("Delete");
    await waitFor(() => expect(mocks.delete).toHaveBeenCalledTimes(1));
    expect(mocks.delete.mock.calls[0][1].data).toEqual({ row_ids: ["alpha-2"], selected_all_rows: false });
    expect(mocks.get).not.toHaveBeenCalled();
  });
});

import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { palette } from "src/theme/palette";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  datasetCaps,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import DevelopBarLeftSection from "../../../DevelopBarRightSection/DevelopBarLeftSection";
import DevelopDataSelectionActive from "../../../DevelopBarRightSection/DevelopDataSelectionActive";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
  useDevelopSelectedRowsStore,
} from "../../../states";
import { useDataGridViewStore } from "../useDataGridView";
import useDataToolbarView from "../useDataToolbarView";

// Keep the real Search rows input, hook, Zustand stores, selection actions,
// resolver and classic write adapters. Only unrelated UI and I/O are stubbed.
const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  refresh: vi.fn(),
  gridRead: vi.fn(),
  gridApi: { current: null },
  noop: () => {},
  Null: () => null,
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    get: (...args) => h.get(...args),
    post: (...args) => h.post(...args),
    delete: (...args) => h.remove(...args),
  },
}));
vi.mock("../../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    gridApi: h.gridApi,
    refreshGrid: h.refresh,
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["w5-a-query-scope", id],
    queryFn: h.get,
    enabled: false,
    initialData: {
      data: { result: { table: [{ row_id: "alpha-1" }, { row_id: "alpha-2" }] } },
    },
  }),
}));
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useAsOfVersion: () => ({ isAsOf: false, isLoading: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner", user: {} }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: h.noop,
  Events: {},
  PropertyName: {},
}));
vi.mock("src/utils/Mixpanel/mixpanel", () => ({ trackEvent: h.noop }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.noop }));
vi.mock("src/components/iconify/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color/svg-color", () => ({ default: h.Null }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.noop }));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: h.noop }));
vi.mock("src/sections/common/CommandPalette/commandRegistry", () => ({
  useRegisterCommands: h.noop,
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, h.noop],
}));
vi.mock("../useDataGridLayout", () => ({
  default: () => ({ openMenu: h.noop }),
}));
vi.mock("../../Versions/useVersionUrl", () => ({
  default: () => ({ toggleHistory: h.noop }),
}));
vi.mock("../../DatapointDrawerV2/useRowViewUrl", () => ({
  default: () => ({ openRow: h.noop }),
}));
vi.mock("../../Splits/SplitFilterButton", () => ({ default: h.Null }));
vi.mock("../ToolbarControls", () => ({ FailedOnlyChip: h.Null }));
vi.mock("../GoToRowPopover", () => ({ default: h.Null }));
vi.mock("../CellPeekPopover", () => ({ default: h.Null }));
vi.mock("src/sections/common/filters/ExpressionFilterButton", () => ({
  default: h.Null,
}));
vi.mock("../../DataMenuList/DataMenuList", () => ({ default: h.Null }));
vi.mock("../../Duplicate/DuplicateRowAction", () => ({ default: h.Null }));
vi.mock("../../Splits/AssignSplitsMenu", () => ({ default: h.Null }));
vi.mock("../../Versions/ArchiveRowsDialog", () => ({ default: h.Null }));
vi.mock("../../Versions/TagRowsDialog", () => ({ default: h.Null }));
vi.mock("../../Versions/RestoreRowsDialog", () => ({
  useRestoreRowsDialog: () => ({ open: h.noop }),
}));
vi.mock("../../Versions/PendingChangesGate", () => ({
  usePendingChangesGate: () => (_kind, proceed) => proceed(),
}));
vi.mock("src/sections/common/export/ExportDialog", () => ({
  DatasetExportDialog: h.Null,
}));
vi.mock("src/sections/annotations/queues/components/add-to-queue-dialog", () => ({
  default: h.Null,
}));
vi.mock("src/sections/common/AddData/SyntheticMoreAction", () => ({
  default: h.Null,
}));
vi.mock("src/sections/common/AddData/GenerateRowsDialog", () => ({
  useGenerateRows: { getState: () => ({ open: h.noop }) },
}));

/* eslint-disable react/prop-types */
vi.mock("../../../Common/CustomDialog/CustomDialog", () => ({
  default: function ActionDialog({ open, title, actionButton, onClickAction, children }) {
    if (!open) return null;
    return (
      <div role="dialog" aria-label={title}>
        {children}
        <button type="button" onClick={onClickAction}>{actionButton}</button>
      </div>
    );
  },
}));
vi.mock("../../RunEvals/RunEvals", () => ({
  default: function EvalPicker({ gridRef, setIsData }) {
    useEffect(() => {
      gridRef.current = {
        api: {
          getSelectedRows: () => [
            { originType: "eval", field: "eval-column" },
            { originType: "run_prompt", field: "prompt-column" },
          ],
        },
      };
      setIsData(true);
    }, [gridRef, setIsData]);
    return null;
  },
}));
/* eslint-enable react/prop-types */

const DATASET = "query-scope-dataset";
const ROWS = {
  alpha: [{ row_id: "alpha-1" }, { row_id: "alpha-2" }],
  beta: [{ row_id: "beta-1" }, { row_id: "beta-2" }],
};
const emptySelection = () => ({ selectAll: false, toggledNodes: [] });
const selection = () => {
  const { selectAll, toggledNodes } = useDevelopSelectedRowsStore.getState();
  return { selectAll, toggledNodes };
};
const theme = createTheme({ palette: palette("light") });
let client;
let gridSelection;
let visibleSearch;
let finishGridRead;

// The production host keeps Search rows present and mounts the bulk bar only
// while selected (DevelopDataRightSection). Do not invent action handlers here.
function SearchAndActions() {
  const { selectAll, toggledNodes } = useDevelopSelectedRowsStore();
  return (
    <>
      <DevelopBarLeftSection hideFilter />
      {(selectAll || toggledNodes.length > 0) && <DevelopDataSelectionActive />}
    </>
  );
}

function mountToolbar() {
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[`/develop/${DATASET}`]}>
          <Routes>
            <Route path="/develop/:dataset" element={<SearchAndActions />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function selectAll(toggledNodes = []) {
  act(() => {
    gridSelection = { selectAll: true, toggledNodes: [...toggledNodes] };
    useDevelopSelectedRowsStore.setState(gridSelection);
  });
}

function openAction(name) {
  fireEvent.click(screen.getByRole("button", { name, exact: true }));
  return within(screen.getByRole("dialog")).getByRole("button", {
    name,
    exact: true,
  });
}

function expectNoWrites() {
  expect(h.remove).not.toHaveBeenCalled();
  expect(h.post).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  setRealBackendForTests(true);
  useDevelopSearchStore.setState({ search: "alpha" });
  useDevelopSelectedRowsStore.setState(emptySelection());
  useDevelopFilterStore.getState().resetFilters();
  useDataGridViewStore.setState({ views: {} });
  gridSelection = emptySelection();
  visibleSearch = "alpha";
  finishGridRead = null;
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false, gcTime: Infinity },
    },
  });
  h.gridApi.current = {
    getServerSideSelectionState: () => gridSelection,
    // Deliberately do not emit selectionChanged: the store must be cleared
    // synchronously even when AG Grid's selection event has not arrived yet.
    deselectAll: vi.fn(() => { gridSelection = emptySelection(); }),
    getGridOption: () => ({ totalRowCount: 2, filteredRowCount: 2 }),
    getRowNode: (id) => ({ data: { row_id: id } }),
    onFilterChanged: vi.fn(() => {
      const query = useDevelopSearchStore.getState().search;
      h.gridRead(query);
      return new Promise((resolve) => {
        finishGridRead = () => {
          visibleSearch = query;
          resolve();
        };
      });
    }),
  };
  h.get.mockImplementation(async (url, options) => {
    if (url !== endpoints.develop.getDatasetDetail(DATASET)) {
      throw new Error(`Unexpected read: ${url}`);
    }
    const query = JSON.parse(options.params.search).key;
    return {
      data: { result: { table: ROWS[query], metadata: { has_more: false } } },
    };
  });
  h.remove.mockResolvedValue({ data: { result: {} } });
  h.post.mockResolvedValue({ data: { result: { rows: 2, cells: 4 } } });
  const noNetwork = () => { throw new Error("Network is forbidden in this test"); };
  vi.stubGlobal("fetch", vi.fn(noNetwork));
  vi.spyOn(XMLHttpRequest.prototype, "open").mockImplementation(noNetwork);
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
  useDevelopSelectedRowsStore.setState(emptySelection());
  useDevelopSearchStore.setState({ search: "" });
});

describe("W5-A-002 launch classic search selection scope", () => {
  it.each([
    ["select-all", []],
    ["select-all with an alpha exclusion", ["alpha-1"]],
  ])("clears %s before publishing beta or starting the debounce", (_label, excluded) => {
    mountToolbar();
    selectAll(excluded);
    const atSearchChange = [];
    const unsubscribe = useDevelopSearchStore.subscribe(({ search }) => {
      atSearchChange.push({ search, store: selection(), grid: gridSelection });
    });
    try {
      fireEvent.change(screen.getByRole("textbox", { name: "Search rows" }), {
        target: { value: "beta" },
      });
    } finally {
      unsubscribe();
    }
    expect(atSearchChange).toEqual([
      { search: "beta", store: emptySelection(), grid: emptySelection() },
    ]);
    expect(h.gridApi.current.deselectAll).toHaveBeenCalledTimes(1);
    expect(selection()).toEqual(emptySelection());
    expect(visibleSearch).toBe("alpha");
    expect(h.gridApi.current.onFilterChanged).not.toHaveBeenCalled();
    expect(h.get).not.toHaveBeenCalled();
    expectNoWrites();
  });

  const actions = ["Delete", "Duplicate", "Run"].flatMap((name) => [
    [name, "select-all", []],
    [name, "select-all with an alpha exclusion", ["alpha-1"]],
  ]);
  it.each(actions)(
    "%s needs fresh beta selection after %s, including an open confirmation",
    async (name, _label, excluded) => {
      expect(datasetCaps()).toMatchObject({
        real: true,
        gridWrites: "classic",
        selectionVersionedActions: false,
      });
      expect(ROWS.alpha).toHaveLength(ROWS.beta.length);
      mountToolbar();
      selectAll(excluded);
      const previousConfirm = openAction(name);
      fireEvent.change(screen.getByRole("textbox", { name: "Search rows" }), {
        target: { value: "beta" },
      });
      // Try to use the old confirmation before the 300 ms debounce. The
      // real selected-row store must unmount it, not leave an actionable bar.
      fireEvent.click(previousConfirm);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expectNoWrites();
      expect(h.get).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      ["Delete", "Duplicate", "Run"].forEach((action) => {
        expect(screen.queryByRole("button", { name: action, exact: true }))
          .not.toBeInTheDocument();
      });
      expect(selection()).toEqual(emptySelection());
      expect(h.gridApi.current.getServerSideSelectionState()).toEqual(emptySelection());
      expect(visibleSearch).toBe("alpha");

      await act(async () => { await vi.advanceTimersByTimeAsync(300); });
      expect(h.gridRead).toHaveBeenCalledWith("beta");
      expect(visibleSearch).toBe("alpha");
      expect(selection()).toEqual(emptySelection());
      expectNoWrites();
      await act(async () => { finishGridRead(); });
      expect(visibleSearch).toBe("beta");
      expect(screen.queryByRole("button", { name, exact: true })).not.toBeInTheDocument();

      selectAll();
      fireEvent.click(openAction(name));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(h.get).toHaveBeenCalledTimes(1);
      expect(JSON.parse(h.get.mock.calls[0][1].params.search).key).toBe("beta");
      const rowIds = ROWS.beta.map((row) => row.row_id);
      if (name === "Delete") {
        expect(h.remove).toHaveBeenCalledExactlyOnceWith(
          endpoints.develop.deleteDatasetRow(DATASET),
          { data: { row_ids: rowIds, selected_all_rows: false } },
        );
        expect(h.post).not.toHaveBeenCalled();
      } else if (name === "Duplicate") {
        expect(h.post).toHaveBeenCalledExactlyOnceWith(
          endpoints.develop.duplicateDatasetRows(DATASET),
          { row_ids: rowIds, num_copies: 1, selected_all_rows: false },
        );
        expect(h.remove).not.toHaveBeenCalled();
      } else {
        expect(h.post).toHaveBeenCalledTimes(2);
        expect(h.post).toHaveBeenCalledWith(endpoints.develop.evaluateRows(), {
          user_eval_metric_ids: ["eval-column"],
          row_ids: rowIds,
          selected_all_rows: false,
        });
        expect(h.post).toHaveBeenCalledWith(endpoints.develop.evaluateRunRows(), {
          run_prompt_ids: ["prompt-column"],
          row_ids: rowIds,
          selected_all_rows: false,
        });
        expect(h.remove).not.toHaveBeenCalled();
      }
    },
  );

  it("clearing Search rows also invalidates the selection", () => {
    mountToolbar();
    selectAll(["alpha-1"]);
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(useDevelopSearchStore.getState().search).toBe("");
    expect(selection()).toEqual(emptySelection());
    expect(gridSelection).toEqual(emptySelection());
    expect(h.gridApi.current.onFilterChanged).not.toHaveBeenCalled();
    expectNoWrites();
  });

  it("clears explicit row ids even if the grid API is unavailable", () => {
    h.gridApi.current = null;
    const { result } = renderHook(() => useDataToolbarView(DATASET), {
      wrapper: MemoryRouter,
    });
    act(() => {
      useDevelopSelectedRowsStore.setState({ selectAll: false, toggledNodes: ["alpha-1"] });
      result.current.setSearchExtended("beta");
    });
    expect(selection()).toEqual(emptySelection());
  });

  it("keeps selection when the search value has not changed", () => {
    const { result } = renderHook(() => useDataToolbarView(DATASET), {
      wrapper: MemoryRouter,
    });
    selectAll(["alpha-1"]);
    act(() => { result.current.setSearchExtended("alpha"); });
    expect(selection()).toEqual({ selectAll: true, toggledNodes: ["alpha-1"] });
    expect(h.gridApi.current.deselectAll).not.toHaveBeenCalled();
  });
});

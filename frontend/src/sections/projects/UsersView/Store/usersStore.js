import { getRandomId } from "src/utils/utils";
import { create } from "zustand";
import { userDefaultFilter } from "../common";

const useUsersStore = create((set, get) => ({
  searchQuery: "",
  columns: [],
  gridApi: null,
  selectedAll: false,
  selectedRowsData: [],
  openUserListFilter: false,
  columnPanelOpen: false,
  openCustomColumnDialog: false,
  selectedUserId: null,
  chartTypes: {}, // { chartId: "line" | "bar" }
  globalChartType: "line",
  isGlobalChartType: false,
  filters: [{ ...userDefaultFilter, id: getRandomId() }],
  // Last sort_params sent by the grid, mirrored here so the export button can
  // match what's on screen (the sort lives in AG Grid's request, not the store).
  sortParams: [],

  setSearchQuery: (query) => set({ searchQuery: query }),
  setFilters: (newFiltersOrUpdater) => {
    if (typeof newFiltersOrUpdater === "function") {
      // Handle functional update like useState does
      set((state) => ({
        filters: newFiltersOrUpdater(state.filters),
      }));
    } else {
      // Handle direct value
      set({ filters: newFiltersOrUpdater });
    }
  },

  setColumns: (columns) => set({ columns }),
  setGridApi: (api) => set({ gridApi: api }),
  setColumnPanelOpen: (value) => set({ columnPanelOpen: value }),
  setSelectedAll: (value) => set({ selectedAll: value }),
  setOpenUserListFilter: (value) => set({ openUserListFilter: value }),
  toggleOpenUserListFilter: () =>
    set((state) => ({ openUserListFilter: !state.openUserListFilter })),
  setSelectedRowsData: (rows) => set({ selectedRowsData: rows }),
  toggleColumnPanel: () =>
    set((state) => ({ columnPanelOpen: !state.columnPanelOpen })),
  clearSelection: () => {
    const { gridApi } = get();
    if (gridApi) {
      gridApi.deselectAll();
    }
    set({
      selectedAll: false,
      selectedRowsData: [],
    });
  },
  resetStore: () =>
    set({
      searchQuery: "",
      selectedUserData: {},
      gridApi: null,
      selectedAll: false,
      selectedRowsData: [],
      openUserListFilter: false,
      filters: [{ ...userDefaultFilter, id: getRandomId() }],
      sortParams: [],
    }),
  updateColumnVisibility: (updatedData) => {
    set((state) => ({
      columns: state.columns.map((col) => ({
        ...col,
        isVisible: updatedData[col.id] ?? col.isVisible,
      })),
    }));
  },
  setOpenCustomColumnDialog: (value) => set({ openCustomColumnDialog: value }),
  addCustomColumns: (newCols) => {
    set((state) => {
      const existingIds = new Set(state.columns.map((c) => c.id));
      const deduped = (newCols || [])
        .filter((c) => !existingIds.has(c.id))
        .map((c) => ({
          ...c,
          isVisible: true,
          groupBy: "Custom Columns",
        }));
      return { columns: [...state.columns, ...deduped] };
    });
  },
  removeCustomColumns: (idsToRemove) => {
    const removeSet = new Set(idsToRemove || []);
    set((state) => ({
      columns: state.columns.filter(
        (c) => !(c.groupBy === "Custom Columns" && removeSet.has(c.id)),
      ),
    }));
  },
  toggleChartType: (id) => {
    const { chartTypes } = get();
    const current = chartTypes[id] || "line";
    const updated = current === "bar" ? "line" : "bar";

    set({
      chartTypes: {
        ...chartTypes,
        [id]: updated,
      },
    });
  },

  toggleGlobalChartType: () => {
    const current = get().globalChartType;
    set({ globalChartType: current === "bar" ? "line" : "bar" });
  },

  setIsGlobalChartType: (value) => set({ isGlobalChartType: value }),
}));

export default useUsersStore;

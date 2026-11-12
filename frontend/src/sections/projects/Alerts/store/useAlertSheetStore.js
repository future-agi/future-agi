import { create } from "zustand";

export const useAlertSheetStore = create((set) => ({
  // State
  selectedRows: [],
  columns: [],
  selectedAll: false,
  excludingIds: new Set(),
  totalRows: 0,
  searchQuery: "",
  alertRuleDetails: null,
  columnDefs: [],
  gridRef: null,

  // Actions

  setSelectedRows: (rows) => set({ selectedRows: rows }),
  setGridRef: (ref) => set({ gridRef: ref }),

  setColumns: (updater) =>
    set((state) => {
      const newColumns =
        typeof updater === "function" ? updater(state.columns) : updater;
      return { columns: newColumns };
    }),

  setSelectedAll: (selectedAll) => set({ selectedAll }),

  // The Issues grid passes a function updater when rows are unticked during
  // select-all, so apply it to the current set rather than storing it.
  setExcludingIds: (updater) =>
    set((state) => ({
      excludingIds:
        typeof updater === "function" ? updater(state.excludingIds) : updater,
    })),

  setTotalRows: (totalRows) => set({ totalRows }),

  setSearchQuery: (query) => set({ searchQuery: query }),

  setAlertRuleDetails: (details) => set({ alertRuleDetails: details }),

  setColumnDefs: (defs) => set({ columnDefs: defs }),

  onSearchQueryChange: (value) => {
    if (typeof value !== "string") return;
    set({ searchQuery: value });
  },

  handleSelectRows: (data) => {
    set({ selectedRows: data });
  },

  // These will be implemented in the hook using the ref
  handleSelectAll: null,
  refreshGrid: null,
  handleCancelSelection: null,

  handleClearSelection: () => {
    set({ selectedRows: [] });
  },
}));

export const resetAlertSheetStoreState = () => {
  useAlertSheetStore.setState({
    selectedRows: [],
    columns: [],
    selectedAll: false,
    excludingIds: new Set(),
    totalRows: 0,
    searchQuery: "",
    alertRuleDetails: null,
    columnDefs: [],
    gridRef: null,
    handleSelectAll: null,
    refreshGrid: null,
    handleCancelSelection: null,
  });
};

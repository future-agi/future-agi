import { create } from "zustand";
import {
  IssueCell,
  LastTriggeredCell,
  StatusCell,
  TrendChartCell,
} from "../components/AlertsListView/AlertCells";
import FilterChipsRenderer from "../../../common/EvalsTasks/Renderers/FilterChipsRenderer";
import EvalsAndTasksCustomTooltip from "../../../common/EvalsTasks/Renderers/EvalsAndTasksCustomToolTip";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "../../../../utils/axios";
import { formatNumberWithCommas } from "../../UsersView/common";
import { getAlertFilterValue, isAlertMuted } from "../common";

const initialColumnDefs = [
  {
    headerName: "Issues",
    field: "name",
    flex: 2,
    sortable: true,
    cellRenderer: IssueCell,
  },
  {
    headerName: "Trend",
    field: "trends",
    flex: 1,
    sortable: false,
    cellRenderer: TrendChartCell,
    hide: true,
    cellRendererParams: (params) => ({
      ...params,
      value: params?.data?.trends?.map((trend) => ({
        timestamp: trend?.timestamp,
        value: trend?.count,
      })),
    }),
  },
  {
    headerName: "Alert Type",
    field: "metricType",
    flex: 1,
    cellStyle: {
      display: "flex",
      alignItems: "center",
    },
  },
  {
    headerName: "Status",
    field: "status",
    flex: 1,
    cellRenderer: StatusCell,
  },
  {
    headerName: "Last Triggered",
    field: "lastTriggered",
    flex: 1,
    sortable: true,
    cellRenderer: LastTriggeredCell,
  },
  {
    headerName: "No. of triggers",
    field: "noOfAlerts",
    flex: 1,
    sortable: true,
    cellStyle: {
      display: "flex",
      alignItems: "center",
    },
    valueFormatter: (params) => formatNumberWithCommas(params.value),
  },
  {
    headerName: "Filters",
    field: "filters",
    flex: 1,
    sortable: false,
    filter: false,
    headerClass: "custom-header-text",
    cellRenderer: FilterChipsRenderer,
    tooltipValueGetter: (params) => {
      const filters = params.value || [];
      return filters.join(", ");
    },
    tooltipComponent: EvalsAndTasksCustomTooltip,
    hide: true,
    valueGetter: (params) => getAlertFilterValue(params?.data),
  },
];

export const useAlertStore = create((set, get) => ({
  // ========== UI STATE ==========
  searchQuery: "",
  openCreateAlerts: false,
  openSelectProjectModal: false,
  openSheetView: null,
  actionModal: { state: false, type: null },
  currentTab: 0,
  duplicateAlertName: "",
  mainPage: false,
  isConfirmationModalOpen: false,

  // ========== SELECTION STATE ==========
  selectedRows: [],
  selectedAll: false,
  excludingIds: new Set(),
  totalRows: 0,
  gridRef: null,

  // ========== DATA STATE ==========
  selectedProject: null,
  alertType: "span_response_time",
  hasData: true,
  currentPageAlertList: [],
  columnDefs: initialColumnDefs,
  columns: [],

  // ========== LOADING STATE ==========
  isDeletingAlerts: false,
  isMutingAlerts: false,

  // ========== UI ACTIONS ==========
  onSearchQueryChange: (query) => {
    if (typeof query !== "string") return;
    set({ searchQuery: query });
  },

  setOpenCreateAlerts: (open) => set({ openCreateAlerts: open }),

  setConfirmationModalOpen: (open) => set({ isConfirmationModalOpen: open }),

  setOpenSelectProjectModal: (open) => set({ openSelectProjectModal: open }),

  setOpenSheetView: (view) => set({ openSheetView: view }),

  setActionModal: (modal) => set({ actionModal: modal }),

  setCurrentTab: (tab) => set({ currentTab: tab }),

  setDuplicateAlertName: (name) => set({ duplicateAlertName: name }),

  handleStartCreatingAlerts: () => {
    const { selectedProject, mainPage, openSheetView } = get();
    if (mainPage && !selectedProject && !openSheetView) {
      set({ openSelectProjectModal: true });
      return;
    }
    set({ openCreateAlerts: true });
  },

  handleCloseCreateAlert: () => {
    const { openSheetView, mainPage } = get();
    if (mainPage && !openSheetView) {
      set({ selectedProject: null });
    }
    set({
      openCreateAlerts: false,
      duplicateAlertName: "",
    });
  },

  handleOpenActionModal: (type) => {
    if (!["delete", "mute", "unmute"].includes(type)) return;
    set({ actionModal: { state: true, type } });
  },

  handleCloseActionModal: () => {
    set((state) => ({ actionModal: { ...state.actionModal, state: false } }));
    setTimeout(() => {
      set((state) => ({ actionModal: { ...state.actionModal, type: null } }));
    }, 200);
  },

  handleOpenSheetView: (id) => set({ openSheetView: id }),

  handleCloseSheetView: () => {
    const { openSheetView } = get();
    set({ openSheetView: null });
    if (!openSheetView) {
      set({ selectedProject: null });
    }
  },

  handleCloseProjectModal: () => set({ openSelectProjectModal: false }),

  handleOpenProjectModal: () => set({ openSelectProjectModal: true }),

  // ========== SELECTION ACTIONS ==========
  setSelectedRows: (rows) => set({ selectedRows: rows }),

  setSelectedAll: (selected) => set({ selectedAll: selected }),

  setExcludingIds: (ids) => set({ excludingIds: ids }),

  setTotalRows: (rows) => set({ totalRows: rows }),

  setGridRef: (ref) => set({ gridRef: ref }),

  handleSelectRows: (data) => set({ selectedRows: data }),

  handleClearSelection: () => set({ selectedRows: [] }),

  hasUnMutedAlerts: () => {
    const { selectedRows } = get();
    return selectedRows?.some((row) => !isAlertMuted(row));
  },

  handleSelectAll: () => {
    const { gridRef } = get();
    const api = gridRef?.current?.api;
    if (!api) return;

    let allSelected = true;
    api.forEachNode((node) => {
      if (!node.isSelected()) {
        allSelected = false;
      }
    });

    if (allSelected) {
      api.deselectAll();
      set({ selectedRows: [] });
    } else {
      const allRows = [];
      api.forEachNode((node) => {
        node.setSelected(true);
        if (node.data) {
          allRows.push(node.data);
        }
      });
      set({ selectedRows: allRows });
    }
  },

  handleRemoveRow: (rowId) => {
    if (!rowId) return;
    const { gridRef, selectedRows } = get();
    const gridApi = gridRef?.current?.api;
    if (!gridApi) return;

    if (selectedRows?.length === 1) {
      gridApi.deselectAll();
      get().handleCloseActionModal();
      get().handleClearSelection();
    }

    gridApi.forEachNode((node) => {
      if (node.isSelected() && node.data.id === rowId) {
        node.setSelected(false);
      }
    });
  },

  handleCancelSelection: () => {
    const { gridRef } = get();
    if (gridRef?.current?.api) {
      gridRef.current.api.deselectAll();
    }
    set({ selectedRows: [], selectedAll: false, excludingIds: new Set() });
  },

  // ========== DATA ACTIONS ==========
  setSelectedProject: (project) => set({ selectedProject: project }),

  setAlertType: (type) => set({ alertType: type }),

  setHasData: (hasData) => set({ hasData }),

  setCurrentPageAlertList: (updater) =>
    set((state) => {
      const newList =
        typeof updater === "function"
          ? updater(state.currentPageAlertList)
          : updater;
      return { currentPageAlertList: newList };
    }),

  setColumnDefs: (defs) => set({ columnDefs: defs }),

  setColumns: (updater) =>
    set((state) => {
      const newColumns =
        typeof updater === "function" ? updater(state.columns) : updater;
      return { columns: newColumns };
    }),

  initializeWithObserveId: (observeId) => {
    if (observeId) {
      set({ selectedProject: observeId });
    }
  },
  initializeWithMainPage: (mainPage) => {
    set({ mainPage: !!mainPage });
  },

  handleProjectChange: (value) => set({ selectedProject: value }),

  handleChangeAlertType: (value) => set({ alertType: value }),

  refreshGrid: () => {
    const { _refreshFn, gridRef } = get();
    if (_refreshFn) {
      _refreshFn();
    } else if (gridRef?.current?.api?.refreshServerSide) {
      gridRef.current.api.refreshServerSide();
    }
  },

  setRefreshFn: (fn) => set({ _refreshFn: fn }),

  currentPageHasMutedAlerts: () => {
    const { currentPageAlertList } = get();
    return currentPageAlertList?.some((alert) => isAlertMuted(alert));
  },

  // ========== API ACTIONS ==========
  deleteAlerts: async (data) => {
    set({ isDeletingAlerts: true });
    try {
      const response = await axios.delete(endpoints.project.createMonitor, {
        data,
      });

      enqueueSnackbar(response?.data?.result || "Alerts deleted successfully", {
        variant: "success",
      });

      get().handleCloseActionModal();
      get().handleCancelSelection();
      get().refreshGrid();

      return { success: true, data: response.data };
    } catch (error) {
      enqueueSnackbar("Failed to delete alerts", { variant: "error" });
      return { success: false, error };
    } finally {
      set({ isDeletingAlerts: false });
    }
  },

  muteAlerts: async (data) => {
    set({ isMutingAlerts: true });
    try {
      const response = await axios.post(endpoints.project.muteAlerts, data);

      enqueueSnackbar(response?.data?.result || "Alerts muted successfully", {
        variant: "success",
      });

      get().handleCloseActionModal();
      get().handleCancelSelection();
      get().refreshGrid();

      return { success: true, data: response.data };
    } catch (error) {
      enqueueSnackbar("Failed to mute alerts", { variant: "error" });
      return { success: false, error };
    } finally {
      set({ isMutingAlerts: false });
    }
  },
}));

export const resetAlertStoreState = () => {
  useAlertStore.setState({
    // UI STATE
    searchQuery: "",
    openCreateAlerts: false,
    openSelectProjectModal: false,
    openSheetView: null,
    actionModal: { state: false, type: null },
    currentTab: 0,
    duplicateAlertName: "",
    mainPage: false,

    // SELECTION STATE
    selectedRows: [],
    selectedAll: false,
    excludingIds: new Set(),
    totalRows: 0,
    gridRef: null,

    // DATA STATE
    selectedProject: null,
    alertType: "span_response_time",
    hasData: true,
    currentPageAlertList: [],
    columnDefs: initialColumnDefs,
    columns: [],
    _refreshFn: null,

    // LOADING STATE
    isDeletingAlerts: false,
    isMutingAlerts: false,
  });
};

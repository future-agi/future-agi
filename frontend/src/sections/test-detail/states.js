import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";
import { getRandomId } from "src/utils/utils";

const defaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

export const useTestDetailStore = create((set, getState) => ({
  search: "",
  setSearch: (search) => set({ search }),
  openFilter: false,
  setOpenFilter: (openFilter) =>
    set({
      openFilter:
        typeof openFilter === "function"
          ? openFilter(getState().openFilter)
          : openFilter,
    }),
  openColumnConfigure: false,
  setOpenColumnConfigure: (openColumnConfigure) => set({ openColumnConfigure }),
  filters: [{ ...defaultFilter, id: getRandomId() }],
  setFilters: (filters) =>
    set({
      filters:
        typeof filters === "function" ? filters(getState().filters) : filters,
    }),
  toggledNodes: [],
  selectAll: false,
  setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
  setSelectAll: (value) => set(() => ({ selectAll: value })),
  reset: () =>
    set({
      search: "",
      openFilter: false,
      openColumnConfigure: false,
      filters: [{ ...defaultFilter, id: getRandomId() }],
      toggledNodes: [],
      selectAll: false,
      selectedFixableRecommendations: [],
      selectedNonFixableRecommendations: [],
      columnDef: [],
      configureEval: null,
      fixMyAgentDrawerOpen: false,
      isFixMyAgentCollapsed: false,
    }),
  configureEval: null,
  setConfigureEval: (value) => set(() => ({ configureEval: value })),
  fixMyAgentDrawerOpen: false,
  setFixMyAgentDrawerOpen: (value) =>
    set(() => ({ fixMyAgentDrawerOpen: value })),
  selectedFixableRecommendations: [],
  toggleSelectedFixableRecommendation: (index, callExecutionIds) =>
    set((state) => ({
      selectedFixableRecommendations: state.selectedFixableRecommendations.find(
        (r) => r.index === index,
      )
        ? state.selectedFixableRecommendations.filter((r) => r.index !== index)
        : [
            ...state.selectedFixableRecommendations,
            { index, callExecutionIds },
          ],
    })),
  selectedNonFixableRecommendations: [],
  toggleSelectedNonFixableRecommendation: (index, callExecutionIds) =>
    set((state) => ({
      selectedNonFixableRecommendations:
        state.selectedNonFixableRecommendations.find((r) => r.index === index)
          ? state.selectedNonFixableRecommendations.filter(
              (r) => r.index !== index,
            )
          : [
              ...state.selectedNonFixableRecommendations,
              { index, callExecutionIds },
            ],
    })),
  columnDef: [],
  setColumnDef: (columnDef) => set(() => ({ columnDef })),
  removeAllFilters: () => {
    set({
      selectedNonFixableRecommendations: [],
      selectedFixableRecommendations: [],
      filters: [{ ...defaultFilter, id: getRandomId() }],
      openFilter: false,
    });
  },
}));

export const useTestDetailSideDrawerStoreShallow = (fun) =>
  useTestDetailSideDrawerStore(useShallow(fun));

export const useTestDetailSideDrawerStore = create((set) => ({
  testDetailDrawerOpen: null,
  evalView: null,
  currentRightTab: "Evaluations",
  currentLeftTab: "Transcript",
  setCurrentRightTab: (tab) => set({ currentRightTab: tab }),
  setCurrentLeftTab: (tab) => set({ currentLeftTab: tab }),
  setTestDetailDrawerOpen: (open, evalView) =>
    set({ testDetailDrawerOpen: open, evalView }),
  setEvalView: (evalView) => set({ evalView }),
  compareReplay: false,
  setCompareReplay: (compareReplay) => set({ compareReplay }),
}));

export const useTestDetailStoreShallow = (fun) =>
  useTestDetailStore(useShallow(fun));

export const useTestDetailSearchStoreShallow = (fun) =>
  useTestDetailStore(useShallow(fun));

// The run status the call grid last read, and the execution it read it for.
// `kpisUpdateCountAtFinish` is how many KPI reads had landed when the grid saw
// the run finish: only a later read holds the final count Debug failures
// judges. `finalKpisFailed` says the final read failed and none landed since.
const EMPTY_EXECUTION_STATE = {
  status: null,
  statusExecutionId: null,
  kpisUpdateCountAtFinish: null,
  finalKpisFailed: false,
};

// Applies `update` only while `executionId` is the execution on screen.
const forExecution = (executionId, update) => (state) =>
  state.statusExecutionId === executionId ? update : {};

export const useTestExecutionStore = create((set) => ({
  ...EMPTY_EXECUTION_STATE,
  setStatus: (executionId, status) =>
    set({ status, statusExecutionId: executionId }),
  // `null` when the run is making calls again.
  setKpisUpdateCountAtFinish: (executionId, count) =>
    set(
      forExecution(executionId, {
        kpisUpdateCountAtFinish: count,
        finalKpisFailed: false,
      }),
    ),
  markFinalKpisFailed: (executionId) =>
    set(forExecution(executionId, { finalKpisFailed: true })),
  // A newly opened execution has no status and no final count yet.
  openExecution: (executionId) =>
    set({ ...EMPTY_EXECUTION_STATE, statusExecutionId: executionId }),
}));

export const resetState = () => {
  useTestDetailSideDrawerStore.setState({
    testDetailDrawerOpen: null,
    evalView: null,
    currentRightTab: "Evaluations",
    currentLeftTab: "Transcript",
    compareReplay: false,
  });
  useTestDetailStore.setState({
    search: "",
  });
  useTestExecutionStore.setState(EMPTY_EXECUTION_STATE);
};

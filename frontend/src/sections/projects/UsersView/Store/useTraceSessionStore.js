// stores/useTraceSessionStore.js
import { create } from "src/utils/zustand";
import { initialSessionVisibility } from "../common";
import { useShallow } from "zustand/shallow";

const useTraceSessionStore = create((set) => ({
  // Tab and UI state
  selectedTab: "traces",
  openColumnConfigure: false,
  cellHeight: "Short",
  openSessionColumnConfigure: false,
  openUserDetailFilter: false,
  isFilterApplied: false,

  // Column state
  traceColumns: [],
  sessionColumns: [],
  sessionUpdateObj: initialSessionVisibility,

  // Actions
  setSelectedTab: (tab) => set({ selectedTab: tab }),
  setCellHeight: (height) => set({ cellHeight: height }),
  toggleOpenUserDetailFilter: () =>
    set((state) => ({ openUserDetailFilter: !state.openUserDetailFilter })),
  setTraceColumns: (columns) => {
    if (typeof columns === "function") {
      set((state) => ({ traceColumns: columns(state.traceColumns) }));
    } else {
      set({ traceColumns: columns });
    }
  },

  setSessionColumns: (columns) => {
    if (typeof columns === "function") {
      set((state) => ({ sessionColumns: columns(state.sessionColumns) }));
    } else {
      set({ sessionColumns: columns });
    }
  },

  setSessionUpdateObj: (obj) => set({ sessionUpdateObj: obj }),
  setOpenColumnConfigure: (open) => set({ openColumnConfigure: open }),
  setOpenSessionColumnConfigure: (open) =>
    set({ openSessionColumnConfigure: open }),
  setIsFilterApplied: (applied) => set({ isFilterApplied: applied }),

  // Compound actions
  resetColumnsOnTabChange: (newTab) =>
    set((state) => ({
      traceColumns: newTab === "traces" ? [] : state.traceColumns,
      sessionColumns: newTab === "sessions" ? [] : state.sessionColumns,
      openColumnConfigure: false,
      openSessionColumnConfigure: false,
    })),

  updateTraceColumnVisibility: (updatedData) => {
    set((state) => ({
      traceColumns: state.traceColumns.map((col) => ({
        ...col,
        isVisible: updatedData[col.id] ?? col.isVisible,
      })),
    }));
  },

  updateSessionColumnVisibility: (updatedData) => {
    set((state) => ({
      sessionColumns: state.sessionColumns.map((col) => ({
        ...col,
        isVisible: updatedData[col.id] ?? col.isVisible,
      })),
      sessionUpdateObj: updatedData,
    }));
  },

  // Reset functionality
  resetTraceSessionStore: () =>
    set({
      selectedTab: "traces",
      openColumnConfigure: false,
      openSessionColumnConfigure: false,
      isFilterApplied: false,
      traceColumns: [],
      sessionColumns: [],
      sessionUpdateObj: initialSessionVisibility,
    }),
}));

export const useTraceSessionStoreShallow = (fun) =>
  useTraceSessionStore(useShallow(fun));

export default useTraceSessionStore;

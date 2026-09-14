import { create } from "zustand";

const useRerunColumnInExperimentInStore = create((set) => ({
  selectedSourceId: null,
  setSelectedSourceId: (id) => set({ selectedSourceId: id }),
}));

export const useRerunColumnInExperimentStoreShallow = (fun) =>
  useRerunColumnInExperimentInStore(fun);

const useColumnSummaryStore = create((set) => ({
  summaryByColumn: {},
  setColumnSummary: (columnId, type) =>
    set((state) => ({
      summaryByColumn: { ...state.summaryByColumn, [columnId]: type },
    })),
  resetColumnSummaries: () => set({ summaryByColumn: {} }),
}));

export const useColumnSummaryStoreShallow = (fun) => useColumnSummaryStore(fun);

export { useColumnSummaryStore };

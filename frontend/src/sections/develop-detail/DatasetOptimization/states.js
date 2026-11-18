import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

/**
 * Zustand store for Dataset Optimization UI state.
 *
 * Note: Navigation state (selectedOptimizationId, selectedTrialId, detailView)
 * is now managed via URL parameters in DatasetOptimizationContainer using useUrlState.
 * This ensures browser history/back button works correctly.
 *
 * This store only contains transient UI state that doesn't need URL persistence.
 */
export const useDatasetOptimizationStore = create((set) => ({
  // Create drawer state
  isCreateDrawerOpen: false,
  setIsCreateDrawerOpen: (isOpen) => set({ isCreateDrawerOpen: isOpen }),

  // Rerun drawer state (stores default values for rerun)
  rerunDefaultValues: null,
  setRerunDefaultValues: (data) => set({ rerunDefaultValues: data }),

  // Grid API reference (for refreshing after create/rerun)
  optimizationGridApi: null,
  setOptimizationGridApi: (api) => set({ optimizationGridApi: api }),
  stopOptimizationId: null,
  setStopOptimizationId: (val) => set({ stopOptimizationId: val }),
  // Reset state
  reset: () =>
    set({
      isCreateDrawerOpen: false,
      rerunDefaultValues: null,
      stopOptimizationId: null,
    }),
}));

export const useDatasetOptimizationStoreShallow = (selector) =>
  useDatasetOptimizationStore(useShallow(selector));

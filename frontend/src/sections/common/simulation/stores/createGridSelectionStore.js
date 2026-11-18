import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

/**
 * Factory function to create a grid selection Zustand store
 * Creates a store with common selection state and actions
 *
 * @param {Object} options - Configuration options
 * @param {boolean} options.trackTotalRowCount - Whether to track total row count (default: false)
 * @returns {Object} Store and shallow hook
 */
export const createGridSelectionStore = ({
  trackTotalRowCount = false,
} = {}) => {
  const initialState = {
    toggledNodes: [],
    selectAll: false,
    ...(trackTotalRowCount && { totalRowCount: 0 }),
  };

  const store = create((set) => ({
    ...initialState,
    setToggledNodes: (value) => set(() => ({ toggledNodes: value })),
    setSelectAll: (value) => set(() => ({ selectAll: value })),
    ...(trackTotalRowCount && {
      setTotalRowCount: (value) => set(() => ({ totalRowCount: value })),
    }),
    reset: () => set(() => initialState),
  }));

  // Create a shallow hook wrapper
  const useStoreShallow = (selector) => store(useShallow(selector));

  return {
    store,
    useStoreShallow,
  };
};

/**
 * Factory function to create an evaluation dialog Zustand store
 * Creates a store to manage evaluation dialog open/close state
 *
 * @param {string} stateName - Name of the open state property (e.g., "openEvaluation", "openTestEvaluation")
 * @returns {Object} Store and shallow hook
 */
export const createEvaluationDialogStore = (stateName = "openEvaluation") => {
  const setterName = `set${stateName.charAt(0).toUpperCase()}${stateName.slice(1)}`;

  const store = create((set) => ({
    [stateName]: false,
    [setterName]: (value) => set({ [stateName]: value }),
  }));

  const useStoreShallow = (selector) => store(useShallow(selector));

  return {
    store,
    useStoreShallow,
  };
};

export default createGridSelectionStore;

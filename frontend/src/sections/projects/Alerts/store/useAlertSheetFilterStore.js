import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import { CLEARED_FILTER_STATE, createFilterSlice } from "./alertFilterState";

export const useAlertSheetFilterStore = create((set) => createFilterSlice(set));

export const useAlertSheetFilterShallow = () =>
  useAlertSheetFilterStore(
    useShallow((state) => ({
      activeFilters: state.activeFilters,
      hasValidFilters: state.hasValidFilters,
      setActiveFilters: state.setActiveFilters,
    })),
  );

export const resetAlertSheetFilterStoreState = () => {
  useAlertSheetFilterStore.setState(CLEARED_FILTER_STATE);
};

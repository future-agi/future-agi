import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

const useToggleAnnotationsStore = create((set) => ({
  showMetricsIds: [],
  toggleMetric: (metricId) =>
    set((state) => ({
      showMetricsIds: state.showMetricsIds.includes(metricId)
        ? state.showMetricsIds.filter((id) => id !== metricId)
        : [...state.showMetricsIds, metricId],
    })),
  reset: () => set({ showMetricsIds: [] }),
}));

export default useToggleAnnotationsStore;

export const useShallowToggleAnnotationsStore = (func) =>
  useToggleAnnotationsStore(useShallow(func));

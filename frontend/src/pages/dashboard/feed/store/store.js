import { create } from "zustand";

export const useFeedDetailStore = create((set) => ({
  currentTraceId: null,
  timeRange: null,
  errorName: null,

  setCurrentTraceId: (traceId) => set({ currentTraceId: traceId }),
  setTimeRange: (range) => set({ timeRange: range }),
  setErrorName: (errorName) => set({ errorName }),
  resetStore: () =>
    set({ currentTraceId: null, timeRange: null, errorName: null }),
}));

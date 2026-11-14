import { create } from "src/utils/zustand";

const useRerunColumnInExperimentInStore = create((set) => ({
  selectedSourceId: null,
  setSelectedSourceId: (id) => set({ selectedSourceId: id }),
}));

export const useRerunColumnInExperimentStoreShallow = (fun) =>
  useRerunColumnInExperimentInStore(fun);

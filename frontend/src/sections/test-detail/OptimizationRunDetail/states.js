import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

const useOptimizationRunDetailStore = create((set) => ({
  openOptimizationRerun: false,
  setOpenOptimizationRerun: (value) => set({ openOptimizationRerun: value }),
}));

export const useOptimizationRunDetailStoreShallow = (fun) =>
  useOptimizationRunDetailStore(useShallow(fun));

export default useOptimizationRunDetailStore;

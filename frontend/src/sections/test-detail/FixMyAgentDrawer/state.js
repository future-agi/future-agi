import { create } from "src/utils/zustand";
import { FixMyAgentDrawerSections } from "./common";
import { useShallow } from "zustand/react/shallow";

export const useFixMyAgentDrawerStore = create((set) => ({
  openSection: { section: FixMyAgentDrawerSections.SUGGESTIONS },
  setOpenSection: (openSection) => set({ openSection }),
  createEditOptimizationOpen: false,
  setCreateEditOptimizationOpen: (value) =>
    set(() => ({ createEditOptimizationOpen: value })),
}));

export const useFixMyAgentDrawerStoreShallow = (fun) =>
  useFixMyAgentDrawerStore(useShallow(fun));

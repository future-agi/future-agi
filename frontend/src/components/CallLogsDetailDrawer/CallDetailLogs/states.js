import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

export const useCallLogsSearchStore = create((set) => ({
  search: "",
  setSearch: (search) => set({ search }),
  level: "",
  setLevel: (level) => set({ level }),
  category: "",
  setCategory: (category) => set({ category }),
  totalCount: 0,
  reset: () => set({ search: "", level: "", category: "", totalCount: 0 }),
}));

export const useCallLogsSearchStoreShallow = (fun) =>
  useCallLogsSearchStore(useShallow(fun));

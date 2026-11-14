import { create } from "src/utils/zustand";

export const useEvalStore = create((set) => ({
  createGroupMode: false,
  EditGroupMode: false,
  selectedEvals: [],
  openCreateEvalGroupDrawer: false,
  setEditGroupMode: (EditGroupMode) => set({ EditGroupMode }),

  setSelectedEvals: (updater) =>
    set((state) => {
      const newList =
        typeof updater === "function" ? updater(state.selectedEvals) : updater;
      return { selectedEvals: newList };
    }),
  setCreateGroupMode: (createGroupMode) => set({ createGroupMode }),
  setOpenCreateGroupDrawer: (openCreateEvalGroupDrawer) =>
    set({ openCreateEvalGroupDrawer }),
}));

export const resetEvalStore = () => {
  useEvalStore.setState({
    createGroupMode: false,
    EditGroupMode: false,
    selectedEvals: [],
    openCreateEvalGroupDrawer: false,
  });
};

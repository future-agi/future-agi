import { create } from "src/utils/zustand";

export const useCallLogsSideDrawerStore = create((set) => ({
  callLogsSideDrawerData: null,
  setCallLogsSideDrawerData: (data) => set({ callLogsSideDrawerData: data }),
  resetCallLogsSideDrawer: () => {
    set({
      callLogsSideDrawerData: null,
    });
  },
}));

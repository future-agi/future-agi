import { create } from "src/utils/zustand";

export const useUserManagementStore = create((set) => ({
  usersList: [],
  setUsersList: (newUsersList) => set({ usersList: newUsersList }),
}));

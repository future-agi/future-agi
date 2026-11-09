import { create } from "zustand";

/** Manage splits dialog: opened from the Splits popover and the cell menu. */
export const useManageSplitsStore = create((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));

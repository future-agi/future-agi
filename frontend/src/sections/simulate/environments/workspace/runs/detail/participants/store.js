import { create } from "src/utils/zustand";
import { useShallow } from "zustand/react/shallow";

// The trials table's view, kept while the drawer moves between trials (each trial remounts the Participants panel).
const useTrialRoutesView = create((set) => ({
  open: false,
  showTools: false,
  setOpen: (open) => set({ open }),
  setShowTools: (showTools) => set({ showTools }),
  reset: () => set({ open: false, showTools: false }),
}));

export default useTrialRoutesView;

export const useShallowTrialRoutesView = (fn) =>
  useTrialRoutesView(useShallow(fn));

export const resetTrialRoutesView = () => useTrialRoutesView.getState().reset();

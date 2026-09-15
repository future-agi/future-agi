import { create } from "zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";

export const useEnvironmentsStore = create(
  devtools(
    (set, get, store) => ({
      choice: null, // OPTION_ID of the open entry card | null
      draft: null, // source object a panel CTA produced | null

      setChoice: (id) => set({ choice: id }, false, "setChoice"),
      clearChoice: () => set({ choice: null }, false, "clearChoice"),
      setDraft: (source) => set({ draft: source }, false, "setDraft"),
      clearDraft: () => set({ draft: null }, false, "clearDraft"),

      reset: () => set(store.getInitialState(), false, "reset"),
    }),
    {
      name: "SimulateEnvironmentsStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useEnvironmentsStoreShallow = (fn) =>
  useEnvironmentsStore(useShallow(fn));

export const resetEnvironmentsStore = () =>
  useEnvironmentsStore.getState().reset();

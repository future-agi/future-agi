import { create } from "src/utils/zustand";
import { devtools } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { CURRENT_ENVIRONMENT } from "src/config-global";

export const useAgentListGridStore = create(
  devtools(
    (set, get, store) => ({
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 0,
      gridApi: null,

      setToggledNodes: (value) =>
        set({ toggledNodes: value }, false, "setToggledNodes"),
      setSelectAll: (value) => set({ selectAll: value }, false, "setSelectAll"),
      setTotalRowCount: (value) =>
        set({ totalRowCount: value }, false, "setTotalRowCount"),
      setGridApi: (api) => set({ gridApi: api }, false, "setGridApi"),

      reset: () => {
        set(store.getInitialState(), false, "reset");
      },
    }),
    {
      name: "AgentListGridStore",
      enabled: CURRENT_ENVIRONMENT !== "production",
    },
  ),
);

export const useAgentListGridStoreShallow = (fun) =>
  useAgentListGridStore(useShallow(fun));

export const resetAgentListGridStore = () => {
  useAgentListGridStore.getState().reset();
};
